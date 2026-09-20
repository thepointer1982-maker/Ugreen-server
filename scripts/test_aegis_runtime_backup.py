#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "aegis_runtime_backup.py"
INSTALL = ROOT / "scripts" / "aegis_runtime_backup_install.sh"
CONFIG = ROOT / "config" / "backup" / "aegis-runtime-backup.json"


def write_key(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(os.urandom(32).hex() + "\n", encoding="ascii")
    path.chmod(0o600)


def main() -> None:
    assert shutil.which("openssl"), "openssl required for runtime backup tests"

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert cfg["schema"] == "aegis-runtime-backup/v1"
    assert cfg["encryption"]["cipher"] == "aes-256-cbc"
    assert cfg["encryption"]["kdf"] == "pbkdf2"
    assert cfg["encryption"]["pbkdf2_iterations"] >= 200000
    assert cfg["encryption"]["integrity"] == "hmac-sha256"
    assert cfg["policy"]["include_models"] is False
    assert cfg["policy"]["include_docker_volumes"] is False
    assert cfg["policy"]["include_git_source"] is False
    assert cfg["policy"]["include_secrets"] is False
    assert cfg["policy"]["network_upload"] is False
    assert cfg["policy"]["restore_in_place"] is False

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        home = root / "home"
        backup = root / "backup"
        keys = root / "keys"
        state = root / "status"

        project = home / ".local/state/aegis-project"
        autonomy = home / ".local/state/aegis-autonomy"
        units = home / ".config/systemd/user"
        model_dir = home / ".ollama/models"
        for path in (project, autonomy, units, model_dir):
            path.mkdir(parents=True, exist_ok=True)

        (project / "context.json").write_text(
            '{"project":"aegis-core","pending":"continue"}\n',
            encoding="utf-8",
        )
        (project / "api.token").write_text("must-not-back-up\n", encoding="utf-8")
        (project / "my-secret.json").write_text("must-not-back-up\n", encoding="utf-8")
        (autonomy / "status.json").write_text(
            '{"health":"healthy"}\n',
            encoding="utf-8",
        )
        (units / "aegis-test.timer").write_text("[Timer]\nOnUnitActiveSec=1h\n", encoding="utf-8")
        (units / "other.timer").write_text("[Timer]\nOnUnitActiveSec=1h\n", encoding="utf-8")
        (model_dir / "weights.bin").write_bytes(b"x" * 1024)

        write_key(keys / "enc.key")
        write_key(keys / "mac.key")

        env = os.environ
        env["AEGIS_REPO_ROOT"] = str(ROOT)
        env["AEGIS_RUNTIME_BACKUP_CONFIG"] = str(CONFIG)
        env["AEGIS_RUNTIME_BACKUP_HOME"] = str(home)
        env["AEGIS_RUNTIME_BACKUP_ROOT"] = str(backup)
        env["AEGIS_RUNTIME_BACKUP_KEY_DIR"] = str(keys)
        env["AEGIS_RUNTIME_BACKUP_STATE_DIR"] = str(state)

        spec = importlib.util.spec_from_file_location("aegis_runtime_backup", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)

        manifest = mod.create_backup()
        assert manifest["schema"] == "aegis-runtime-backup-manifest/v1"
        assert manifest["contains_models"] is False
        assert manifest["contains_docker_volumes"] is False
        assert manifest["contains_git_source"] is False
        assert manifest["contains_secrets_by_policy"] is False
        assert manifest["network_upload"] is False
        assert manifest["restore_in_place"] is False
        assert manifest["file_count"] == 3

        encrypted = backup / manifest["backup"]
        assert encrypted.is_file()
        assert encrypted.stat().st_mode & 0o077 == 0
        assert not list(backup.glob("*.tar.gz"))
        assert not list(backup.glob("*.tar"))

        verified = mod.verify_cipher(encrypted)
        assert verified["status"] == "verified"
        assert verified["file_count"] == 3

        restore = root / "restore"
        restored = mod.restore_backup(encrypted, restore)
        assert restored["status"] == "restored-staging"
        assert restored["in_place"] is False
        assert restored["restored_files"] == 3

        assert (
            restore / ".local/state/aegis-project/context.json"
        ).read_text(encoding="utf-8").startswith('{"project":"aegis-core"')
        assert (
            restore / ".local/state/aegis-autonomy/status.json"
        ).is_file()
        assert (
            restore / ".config/systemd/user/aegis-test.timer"
        ).is_file()
        assert not (
            restore / ".local/state/aegis-project/api.token"
        ).exists()
        assert not (
            restore / ".local/state/aegis-project/my-secret.json"
        ).exists()
        assert not (
            restore / ".config/systemd/user/other.timer"
        ).exists()
        assert not (restore / ".ollama/models/weights.bin").exists()

        nonempty = mod.restore_backup(encrypted, restore)
        assert nonempty["status"] == "blocked"
        assert nonempty["reason"] == "restore-target-not-empty"

        raw_cipher = bytearray(encrypted.read_bytes())
        assert len(raw_cipher) > 64
        raw_cipher[-17] ^= 0x01
        encrypted.write_bytes(bytes(raw_cipher))
        tampered = mod.verify_cipher(encrypted)
        assert tampered["status"] == "blocked"
        assert tampered["reason"] == "cipher-sha256-mismatch"

        outer = mod.manifest_for(encrypted)
        data = json.loads(outer.read_text(encoding="utf-8"))
        data["cipher_sha256"] = mod.sha256_file(encrypted)
        outer.write_text(json.dumps(data), encoding="utf-8")
        tampered_hmac = mod.verify_cipher(encrypted)
        assert tampered_hmac["status"] == "blocked"
        assert tampered_hmac["reason"] == "hmac-mismatch"

    install = INSTALL.read_text(encoding="utf-8")
    assert "openssl rand -hex 32" in install
    assert "chmod 600" in install
    assert "OnUnitActiveSec=1d" in install
    assert "RestrictAddressFamilies=AF_UNIX" in install
    assert "NoNewPrivileges=true" in install
    assert "ProtectHome=read-only" in install
    assert "ReadWritePaths=$BACKUP_ROOT $STATE_DIR" in install
    assert "ReadOnlyPaths=$KEY_DIR" in install
    assert "curl" not in install
    assert "wget" not in install
    assert "rsync" not in install
    assert "scp" not in install

    source = SCRIPT.read_text(encoding="utf-8")
    assert "w|gz" in source
    assert "restore-target-not-empty" in source
    assert "hmac.compare_digest" in source
    assert "network_upload" in source
    assert "docker/volumes/" in CONFIG.read_text(encoding="utf-8")
    assert ".ollama/" in CONFIG.read_text(encoding="utf-8")

    print("AEGIS ENCRYPTED RUNTIME BACKUP TESTS PASS")


if __name__ == "__main__":
    main()
