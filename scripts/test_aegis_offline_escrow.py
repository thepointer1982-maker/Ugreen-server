#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "aegis_offline_escrow.py"

spec = importlib.util.spec_from_file_location("escrow", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        repo = root / "repo"
        escrow = root / "escrow"
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "user.name", "AEGIS Test")
        (repo / "README.md").write_text("offline\n", encoding="utf-8")
        git(repo, "add", "README.md")
        git(repo, "commit", "-m", "initial")

        result = mod.create_bundle(repo, escrow)
        bundle = escrow / result["bundle"]
        manifest = bundle.with_suffix(".manifest.json")

        assert bundle.is_file()
        assert manifest.is_file()
        assert result["contains_runtime_state"] is False
        assert result["contains_secrets_by_design"] is False
        assert result["sha256"] == mod.sha256(bundle)
        assert result["head"] == mod.repo_head(repo)

        verified = mod.verify_bundle(bundle)
        assert verified["status"] == "verified"
        assert verified["sha256"] == result["sha256"]

        clone = root / "restore"
        subprocess.run(
            ["git", "clone", str(bundle), str(clone)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert (clone / "README.md").read_text(encoding="utf-8") == "offline\n"

        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["sha256"] = "0" * 64
        manifest.write_text(json.dumps(data), encoding="utf-8")
        blocked = mod.verify_bundle(bundle)
        assert blocked["status"] == "blocked"
        assert blocked["reason"] == "sha256-mismatch"

    install = (
        ROOT / "scripts" / "aegis_offline_escrow_install.sh"
    ).read_text(encoding="utf-8")
    assert "OnUnitActiveSec=7d" in install
    assert "RestrictAddressFamilies=AF_UNIX" in install
    assert "NoNewPrivileges=true" in install
    assert "ReadWritePaths=$ESCROW_ROOT" in install
    assert "curl" not in install
    assert "wget" not in install

    print("AEGIS OFFLINE ESCROW TESTS PASS")


if __name__ == "__main__":
    main()
