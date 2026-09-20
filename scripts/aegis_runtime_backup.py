#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import hmac
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aegis_provenance import attach_provenance

REPO_ROOT = Path(
    os.environ.get("AEGIS_REPO_ROOT", Path(__file__).resolve().parents[1])
).resolve()
CONFIG_FILE = Path(
    os.environ.get(
        "AEGIS_RUNTIME_BACKUP_CONFIG",
        REPO_ROOT / "config" / "backup" / "aegis-runtime-backup.json",
    )
)
HOME_ROOT = Path(os.environ.get("AEGIS_RUNTIME_BACKUP_HOME", str(Path.home()))).resolve()
BACKUP_ROOT = Path(
    os.environ.get(
        "AEGIS_RUNTIME_BACKUP_ROOT",
        str(Path.home() / "aegis-backups" / "runtime"),
    )
).resolve()
KEY_DIR = Path(
    os.environ.get(
        "AEGIS_RUNTIME_BACKUP_KEY_DIR",
        str(Path.home() / ".config" / "aegis-runtime-backup"),
    )
).resolve()
ENC_KEY_FILE = KEY_DIR / "enc.key"
MAC_KEY_FILE = KEY_DIR / "mac.key"
STATE_DIR = Path(
    os.environ.get(
        "AEGIS_RUNTIME_BACKUP_STATE_DIR",
        str(Path.home() / ".local" / "state" / "aegis-runtime-backup"),
    )
).resolve()
STATUS_FILE = STATE_DIR / "status.json"
INNER_MANIFEST = "AEGIS_RUNTIME_BACKUP_MANIFEST.json"


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat()


def load_config() -> dict[str, Any]:
    value = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if value.get("schema") != "aegis-runtime-backup/v1":
        raise ValueError("invalid runtime backup config schema")
    policy = value.get("policy", {})
    required_false = (
        "include_models",
        "include_docker_volumes",
        "include_git_source",
        "include_secrets",
        "network_upload",
        "restore_in_place",
    )
    for key in required_false:
        if policy.get(key) is not False:
            raise ValueError(f"unsafe runtime backup policy: {key}")
    return value


def read_secret_hex(path: Path) -> bytes:
    raw = path.read_text(encoding="ascii").strip()
    if len(raw) != 64:
        raise ValueError(f"invalid key length: {path}")
    try:
        value = bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError(f"invalid hex key: {path}") from exc
    if len(value) != 32:
        raise ValueError(f"invalid key bytes: {path}")
    return value


def key_fingerprint() -> str:
    enc = read_secret_hex(ENC_KEY_FILE)
    mac = read_secret_hex(MAC_KEY_FILE)
    return hashlib.sha256(b"aegis-runtime-keys-v1\0" + enc + mac).hexdigest()[:24]


def derive_mac_key() -> bytes:
    master = read_secret_hex(MAC_KEY_FILE)
    return hmac.new(master, b"aegis-runtime-backup-hmac-v1", hashlib.sha256).digest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hmac_file(path: Path) -> str:
    digest = hmac.new(derive_mac_key(), digestmod=hashlib.sha256)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(raw, 0o600)
        os.replace(raw, path)
    finally:
        try:
            os.unlink(raw)
        except FileNotFoundError:
            pass


def is_excluded(rel: PurePosixPath, config: dict[str, Any]) -> bool:
    rel_text = rel.as_posix()
    name = rel.name
    for fragment in config.get("exclude_path_fragments", []):
        if str(fragment) in rel_text:
            return True
    for pattern in config.get("exclude_name_patterns", []):
        if fnmatch.fnmatch(name, str(pattern)):
            return True
    return False


def included_roots(config: dict[str, Any]) -> list[tuple[Path, PurePosixPath]]:
    roots: list[tuple[Path, PurePosixPath]] = []
    prefix = str(config.get("systemd_name_prefix") or "aegis-")
    for entry in config.get("include", []):
        rel = PurePosixPath(str(entry))
        source = HOME_ROOT.joinpath(*rel.parts)
        if not source.exists():
            continue
        if rel.as_posix() == ".config/systemd/user":
            if not source.is_dir():
                continue
            for child in sorted(source.iterdir()):
                if (
                    child.is_file()
                    and not child.is_symlink()
                    and child.name.startswith(prefix)
                    and not is_excluded(rel / child.name, config)
                ):
                    roots.append((child, rel / child.name))
            continue
        roots.append((source, rel))
    return roots


def iter_backup_files(
    config: dict[str, Any],
) -> list[tuple[Path, PurePosixPath]]:
    result: list[tuple[Path, PurePosixPath]] = []
    seen: set[str] = set()

    for source, rel_base in included_roots(config):
        if source.is_symlink():
            continue
        if source.is_file():
            key = rel_base.as_posix()
            if key not in seen and not is_excluded(rel_base, config):
                result.append((source, rel_base))
                seen.add(key)
            continue

        if not source.is_dir():
            continue

        for root, dirs, files in os.walk(source, followlinks=False):
            root_path = Path(root)
            kept_dirs: list[str] = []
            for directory in sorted(dirs):
                full = root_path / directory
                rel = rel_base / full.relative_to(source).as_posix()
                if full.is_symlink() or is_excluded(rel, config):
                    continue
                kept_dirs.append(directory)
            dirs[:] = kept_dirs

            for filename in sorted(files):
                full = root_path / filename
                if full.is_symlink() or not full.is_file():
                    continue
                rel = rel_base / full.relative_to(source).as_posix()
                if is_excluded(rel, config):
                    continue
                key = rel.as_posix()
                if key in seen:
                    continue
                result.append((full, rel))
                seen.add(key)

    return sorted(result, key=lambda row: row[1].as_posix())


def ensure_target_safe(config: dict[str, Any]) -> None:
    roots = [source.resolve() for source, _ in included_roots(config)]
    backup = BACKUP_ROOT.resolve()
    keys = KEY_DIR.resolve()

    for source in roots:
        try:
            backup.relative_to(source)
        except ValueError:
            pass
        else:
            raise ValueError("backup target is inside a protected source tree")

    try:
        keys.relative_to(backup)
    except ValueError:
        pass
    else:
        raise ValueError("key directory must not be inside backup target")


def openssl_encrypt_command(config: dict[str, Any]) -> list[str]:
    enc = config["encryption"]
    if enc.get("cipher") != "aes-256-cbc" or enc.get("kdf") != "pbkdf2":
        raise ValueError("unsupported encryption contract")
    iterations = int(enc.get("pbkdf2_iterations") or 200000)
    if iterations < 100000:
        raise ValueError("PBKDF2 iteration count too low")
    return [
        "openssl",
        "enc",
        "-aes-256-cbc",
        "-salt",
        "-pbkdf2",
        "-iter",
        str(iterations),
        "-md",
        "sha256",
        "-pass",
        f"file:{ENC_KEY_FILE}",
    ]


def openssl_decrypt_command(config: dict[str, Any], encrypted: Path) -> list[str]:
    cmd = openssl_encrypt_command(config)
    return [
        *cmd[:2],
        "-d",
        *cmd[2:],
        "-in",
        str(encrypted),
    ]


def add_bytes(
    tar: tarfile.TarFile,
    name: str,
    payload: bytes,
    mode: int = 0o600,
) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(payload)
    info.mode = mode
    info.mtime = int(now().timestamp())
    tar.addfile(info, io.BytesIO(payload))


def create_backup() -> dict[str, Any]:
    config = load_config()
    ensure_target_safe(config)

    for command in ("openssl",):
        if shutil.which(command) is None:
            raise RuntimeError(f"{command} missing")
    for key in (ENC_KEY_FILE, MAC_KEY_FILE):
        if not key.is_file():
            raise RuntimeError(f"backup key missing: {key}")
        if key.stat().st_mode & 0o077:
            raise RuntimeError(f"backup key permissions too broad: {key}")
        read_secret_hex(key)

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUP_ROOT, 0o700)
    files = iter_backup_files(config)
    stamp = now().strftime("%Y%m%dT%H%M%SZ")
    encrypted = BACKUP_ROOT / f"aegis-runtime-{stamp}.tar.gz.enc"
    outer_manifest = encrypted.with_suffix(encrypted.suffix + ".manifest.json")

    inner = {
        "schema": "aegis-runtime-backup-inner/v1",
        "created_at": now_iso(),
        "home_relative": True,
        "file_count": len(files),
        "files": [rel.as_posix() for _, rel in files],
        "policy": config.get("policy", {}),
        "key_fingerprint": key_fingerprint(),
    }

    fd, tmp_raw = tempfile.mkstemp(prefix="runtime.", suffix=".enc", dir=str(BACKUP_ROOT))
    os.close(fd)
    tmp = Path(tmp_raw)
    os.chmod(tmp, 0o600)

    proc = subprocess.Popen(
        openssl_encrypt_command(config) + ["-out", str(tmp)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        if proc.stdin is None:
            raise RuntimeError("openssl stdin unavailable")
        with tarfile.open(fileobj=proc.stdin, mode="w|gz", format=tarfile.PAX_FORMAT) as tar:
            add_bytes(
                tar,
                INNER_MANIFEST,
                (json.dumps(inner, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            )
            for source, rel in files:
                tar.add(
                    source,
                    arcname=rel.as_posix(),
                    recursive=False,
                    filter=lambda info: _sanitize_tarinfo(info),
                )
        proc.stdin.close()
        stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"openssl encryption failed: {stderr[-500:]}")
        os.replace(tmp, encrypted)
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
        tmp.unlink(missing_ok=True)

    cipher_sha = sha256_file(encrypted)
    mac = hmac_file(encrypted)
    manifest = {
        "schema": "aegis-runtime-backup-manifest/v1",
        "created_at": inner["created_at"],
        "backup": encrypted.name,
        "cipher_sha256": cipher_sha,
        "hmac_sha256": mac,
        "key_fingerprint": inner["key_fingerprint"],
        "file_count": len(files),
        "size_bytes": encrypted.stat().st_size,
        "cipher": config["encryption"]["cipher"],
        "kdf": config["encryption"]["kdf"],
        "pbkdf2_iterations": config["encryption"]["pbkdf2_iterations"],
        "integrity": "hmac-sha256",
        "contains_models": False,
        "contains_docker_volumes": False,
        "contains_git_source": False,
        "contains_secrets_by_policy": False,
        "network_upload": False,
        "restore_in_place": False,
    }
    atomic_json(outer_manifest, manifest)
    os.chmod(encrypted, 0o600)
    prune_backups(int(config.get("retention") or 14))

    status = attach_provenance(
        {
            "schema": "aegis-runtime-backup-status/v1",
            "generated_at": now_iso(),
            "health": "healthy",
            "last_backup": str(encrypted),
            "manifest": str(outer_manifest),
            "cipher_sha256": cipher_sha,
            "key_fingerprint": inner["key_fingerprint"],
            "file_count": len(files),
            "size_bytes": encrypted.stat().st_size,
            "encrypted": True,
            "network_upload": False,
        },
        kind="runtime-backup-status",
    )
    atomic_json(STATUS_FILE, status)
    return manifest


def _sanitize_tarinfo(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if not info.isfile():
        return None
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode &= 0o777
    return info


def manifest_for(encrypted: Path) -> Path:
    return encrypted.with_suffix(encrypted.suffix + ".manifest.json")


def verify_cipher(encrypted: Path) -> dict[str, Any]:
    config = load_config()
    manifest_path = manifest_for(encrypted)
    if not encrypted.is_file() or not manifest_path.is_file():
        return {"status": "blocked", "reason": "backup-or-manifest-missing"}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "blocked", "reason": "manifest-invalid"}

    if manifest.get("schema") != "aegis-runtime-backup-manifest/v1":
        return {"status": "blocked", "reason": "manifest-schema-invalid"}

    try:
        expected_fingerprint = key_fingerprint()
    except Exception:
        return {"status": "blocked", "reason": "backup-keys-unavailable"}

    if manifest.get("key_fingerprint") != expected_fingerprint:
        return {"status": "blocked", "reason": "wrong-backup-key-set"}

    actual_sha = sha256_file(encrypted)
    if not hmac.compare_digest(str(manifest.get("cipher_sha256") or ""), actual_sha):
        return {"status": "blocked", "reason": "cipher-sha256-mismatch"}

    actual_mac = hmac_file(encrypted)
    if not hmac.compare_digest(str(manifest.get("hmac_sha256") or ""), actual_mac):
        return {"status": "blocked", "reason": "hmac-mismatch"}

    proc = subprocess.Popen(
        openssl_decrypt_command(config, encrypted),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    inner: dict[str, Any] | None = None
    count = 0
    try:
        if proc.stdout is None:
            raise RuntimeError("openssl stdout unavailable")
        with tarfile.open(fileobj=proc.stdout, mode="r|gz") as tar:
            for member in tar:
                validate_member(member)
                if member.name == INNER_MANIFEST:
                    handle = tar.extractfile(member)
                    if handle is None:
                        raise RuntimeError("inner manifest unreadable")
                    inner = json.loads(handle.read().decode("utf-8"))
                elif member.isfile():
                    count += 1
                    handle = tar.extractfile(member)
                    if handle is not None:
                        while handle.read(1024 * 1024):
                            pass
        stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        rc = proc.wait()
        if rc != 0:
            return {
                "status": "blocked",
                "reason": "decrypt-or-archive-verify-failed",
                "detail": stderr[-300:],
            }
    finally:
        if proc.poll() is None:
            proc.kill()

    if not inner or inner.get("schema") != "aegis-runtime-backup-inner/v1":
        return {"status": "blocked", "reason": "inner-manifest-missing"}
    if inner.get("key_fingerprint") != expected_fingerprint:
        return {"status": "blocked", "reason": "inner-key-fingerprint-mismatch"}
    if int(inner.get("file_count") or -1) != count:
        return {"status": "blocked", "reason": "file-count-mismatch"}

    return {
        "status": "verified",
        "backup": str(encrypted),
        "cipher_sha256": actual_sha,
        "key_fingerprint": expected_fingerprint,
        "file_count": count,
        "created_at": inner.get("created_at"),
    }


def validate_member(member: tarfile.TarInfo) -> None:
    pure = PurePosixPath(member.name)
    if pure.is_absolute() or ".." in pure.parts:
        raise RuntimeError("unsafe archive member path")
    if member.issym() or member.islnk() or member.isdev() or member.isfifo():
        raise RuntimeError("unsafe archive member type")


def restore_backup(encrypted: Path, restore_root: Path) -> dict[str, Any]:
    verified = verify_cipher(encrypted)
    if verified.get("status") != "verified":
        return verified

    restore_root = restore_root.resolve()
    if restore_root.exists() and any(restore_root.iterdir()):
        return {"status": "blocked", "reason": "restore-target-not-empty"}
    restore_root.mkdir(parents=True, exist_ok=True)
    os.chmod(restore_root, 0o700)

    config = load_config()
    proc = subprocess.Popen(
        openssl_decrypt_command(config, encrypted),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    restored = 0
    try:
        if proc.stdout is None:
            raise RuntimeError("openssl stdout unavailable")
        with tarfile.open(fileobj=proc.stdout, mode="r|gz") as tar:
            for member in tar:
                validate_member(member)
                if member.name == INNER_MANIFEST:
                    continue
                if not member.isfile():
                    continue
                target = restore_root.joinpath(*PurePosixPath(member.name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                handle = tar.extractfile(member)
                if handle is None:
                    raise RuntimeError("archive member unreadable")
                with target.open("wb") as out:
                    shutil.copyfileobj(handle, out, length=1024 * 1024)
                os.chmod(target, member.mode & 0o777)
                restored += 1
        stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        rc = proc.wait()
        if rc != 0:
            shutil.rmtree(restore_root, ignore_errors=True)
            return {
                "status": "blocked",
                "reason": "restore-decryption-failed",
                "detail": stderr[-300:],
            }
    finally:
        if proc.poll() is None:
            proc.kill()

    return {
        "status": "restored-staging",
        "backup": str(encrypted),
        "restore_root": str(restore_root),
        "restored_files": restored,
        "in_place": False,
    }


def prune_backups(keep: int) -> None:
    keep = max(1, keep)
    backups = sorted(
        BACKUP_ROOT.glob("aegis-runtime-*.tar.gz.enc"),
        key=lambda path: path.stat().st_mtime,
    )
    for old in backups[:-keep]:
        manifest_for(old).unlink(missing_ok=True)
        old.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create")
    verify = sub.add_parser("verify")
    verify.add_argument("backup", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--restore-root", type=Path, required=True)
    args = parser.parse_args()

    try:
        if args.cmd == "create":
            result = create_backup()
        elif args.cmd == "verify":
            result = verify_cipher(args.backup.resolve())
        else:
            result = restore_backup(
                args.backup.resolve(),
                args.restore_root.resolve(),
            )
    except Exception as exc:
        result = {
            "status": "blocked",
            "reason": f"{type(exc).__name__}:{exc}",
        }

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") in {
        "verified",
        "restored-staging",
    } or result.get("schema") == "aegis-runtime-backup-manifest/v1" else 2


if __name__ == "__main__":
    raise SystemExit(main())
