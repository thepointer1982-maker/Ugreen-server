#!/usr/bin/env python3
from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "aegis_deploy_local.sh"

def main() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert "local-only" in result.stdout
    assert "--mode user|system" in result.stdout

    result = subprocess.run(
        ["bash", str(SCRIPT), "--mode", "invalid"],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "Invalid --mode" in result.stderr
    print("AEGIS LOCAL DEPLOY TESTS PASS")

if __name__ == "__main__":
    main()
