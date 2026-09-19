#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "aegis_access_bootstrap.sh"

def main():
    p = subprocess.run(["bash", str(SCRIPT), "--help"], text=True, capture_output=True)
    assert p.returncode == 0
    assert "never open router ports" in p.stdout

    env = os.environ.copy()
    env.pop("AEGIS_RUNNER_TOKEN", None)
    p = subprocess.run(["bash", str(SCRIPT), "runner"], text=True, capture_output=True, env=env)
    assert p.returncode == 4
    assert "AEGIS_RUNNER_TOKEN missing" in p.stderr
    assert "AEGIS ACCESS PREFLIGHT" in p.stdout

    text = SCRIPT.read_text(encoding="utf-8")
    assert "aegis_runner_install.sh" in text
    assert "aegis_tailscale_bridge.sh" in text
    assert "tailscale ip -4" in text
    assert "ssh -p" in text
    print("AEGIS ACCESS BOOTSTRAP TESTS PASS")

if __name__ == "__main__":
    main()
