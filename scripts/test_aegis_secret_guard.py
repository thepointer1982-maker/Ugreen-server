#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "aegis_secret_guard.py"

spec = importlib.util.spec_from_file_location("aegis_secret_guard", SCRIPT)
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
        repo = Path(raw)
        git(repo, "init")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "user.name", "AEGIS Test")

        safe = repo / "safe.txt"
        safe.write_text(
            "OPENAI_API_KEY=not-configured\n"
            "CODEX_ACCESS_TOKEN=disabled\n",
            encoding="utf-8",
        )
        git(repo, "add", "safe.txt")
        assert mod.scan_file(safe) == []

        secret = repo / "secret.txt"
        token = "ghp_" + ("A" * 30)
        secret.write_text("token=" + token + "\n", encoding="utf-8")
        git(repo, "add", "secret.txt")
        findings = mod.scan_file(secret)
        assert findings == [("github-classic-pat", 1)]

        private = repo / "key.txt"
        key_header = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
        private.write_text(key_header + "\n", encoding="utf-8")
        git(repo, "add", "key.txt")
        findings = mod.scan_file(private)
        assert findings == [("private-key", 1)]

        files = {p.name for p in mod.tracked_files(repo)}
        assert files == {"safe.txt", "secret.txt", "key.txt"}

    source = SCRIPT.read_text(encoding="utf-8")
    assert "Secret values are intentionally not printed." in source
    assert "git" in source
    assert "ls-files" in source
    print("AEGIS SECRET GUARD TESTS PASS")


if __name__ == "__main__":
    main()
