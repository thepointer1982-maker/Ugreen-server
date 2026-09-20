#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(
    os.environ.get("AEGIS_REPO_ROOT", Path(__file__).resolve().parents[1])
).resolve()
ESCROW_ROOT = Path(
    os.environ.get(
        "AEGIS_ESCROW_ROOT",
        Path.home() / ".local/share/aegis-escrow",
    )
).resolve()
KEEP = int(os.environ.get("AEGIS_ESCROW_KEEP", "8"))


def now() -> datetime:
    return datetime.now(timezone.utc)


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def repo_head(repo: Path) -> str:
    cp = run(["git", "rev-parse", "HEAD"], cwd=repo)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or "git rev-parse failed")
    value = cp.stdout.strip()
    if len(value) != 40:
        raise RuntimeError("invalid git head")
    return value


def repo_clean(repo: Path) -> bool:
    cp = run(["git", "status", "--porcelain"], cwd=repo)
    return cp.returncode == 0 and not cp.stdout.strip()


def prune_old(root: Path, keep: int) -> None:
    keep = max(1, keep)
    bundles = sorted(root.glob("aegis-*.bundle"), key=lambda p: p.stat().st_mtime)
    for old in bundles[:-keep]:
        manifest = old.with_suffix(".manifest.json")
        old.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)


def create_bundle(repo: Path, root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    head = repo_head(repo)
    stamp = now().strftime("%Y%m%dT%H%M%SZ")
    bundle = root / f"aegis-{stamp}-{head[:12]}.bundle"
    manifest = bundle.with_suffix(".manifest.json")

    fd, tmp_raw = tempfile.mkstemp(prefix="bundle.", dir=str(root))
    os.close(fd)
    tmp = Path(tmp_raw)
    try:
        cp = run(
            ["git", "bundle", "create", str(tmp), "--all"],
            cwd=repo,
        )
        if cp.returncode != 0:
            raise RuntimeError(cp.stderr.strip() or "git bundle create failed")

        verify = run(["git", "bundle", "verify", str(tmp)], cwd=repo)
        if verify.returncode != 0:
            raise RuntimeError(verify.stderr.strip() or "git bundle verify failed")

        os.chmod(tmp, 0o600)
        os.replace(tmp, bundle)
    finally:
        tmp.unlink(missing_ok=True)

    digest = sha256(bundle)
    payload = {
        "schema": "aegis-offline-escrow/v1",
        "created_at": now().isoformat(),
        "head": head,
        "repo_clean": repo_clean(repo),
        "bundle": bundle.name,
        "sha256": digest,
        "size_bytes": bundle.stat().st_size,
        "contains_git_history": True,
        "contains_runtime_state": False,
        "contains_secrets_by_design": False,
        "restore_hint": "git clone <bundle> <target>",
    }
    manifest.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(manifest, 0o600)
    prune_old(root, KEEP)
    return payload


def verify_bundle(bundle: Path) -> dict[str, Any]:
    manifest = bundle.with_suffix(".manifest.json")
    if not bundle.is_file() or not manifest.is_file():
        return {"status": "blocked", "reason": "bundle-or-manifest-missing"}

    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "blocked", "reason": "manifest-invalid"}

    expected = str(data.get("sha256") or "")
    actual = sha256(bundle)
    if expected != actual:
        return {
            "status": "blocked",
            "reason": "sha256-mismatch",
            "expected": expected,
            "actual": actual,
        }

    cp = run(["git", "bundle", "verify", str(bundle)])
    if cp.returncode != 0:
        return {
            "status": "blocked",
            "reason": "git-bundle-verify-failed",
        }

    return {
        "status": "verified",
        "bundle": str(bundle),
        "head": data.get("head"),
        "sha256": actual,
        "size_bytes": bundle.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create")
    verify = sub.add_parser("verify")
    verify.add_argument("bundle", type=Path)
    args = parser.parse_args()

    if args.cmd == "create":
        result = create_bundle(REPO_ROOT, ESCROW_ROOT)
    else:
        result = verify_bundle(args.bundle.resolve())

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") != "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
