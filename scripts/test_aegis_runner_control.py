#!/usr/bin/env python3
from __future__ import annotations
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "aegis_runner_install.sh"
WORKFLOW = SCRIPT.parent.parent / ".github" / "workflows" / "aegis-nas-control.yml"

def main():
    p = subprocess.run(["bash", str(SCRIPT), "--help"], text=True, capture_output=True)
    assert p.returncode == 0
    assert "token is never written" in p.stdout

    p = subprocess.run(["bash", str(SCRIPT)], text=True, capture_output=True, env={})
    assert p.returncode == 4
    assert "AEGIS_RUNNER_TOKEN missing" in p.stderr

    w = WORKFLOW.read_text(encoding="utf-8")
    assert "runs-on: [self-hosted, linux, x64, aegis-ugreen]" in w
    assert "github.actor == 'thepointer1982-maker'" in w
    assert "pull_request:" not in w
    assert "deploy-retry" in w and "real-cycle" in w and "status" in w
    assert "curl " not in w
    print("AEGIS RUNNER CONTROL TESTS PASS")

if __name__ == "__main__":
    main()
