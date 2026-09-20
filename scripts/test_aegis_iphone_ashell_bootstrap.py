#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "aegis_iphone_ashell_bootstrap.sh"

def main():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "aegis-iphone-bootstrap/v1" in text
    assert "ip.is_private" in text
    assert "StrictHostKeyChecking=ask" in text
    assert "ConnectTimeout=10" in text
    assert "ServerAliveInterval=10" in text
    assert "AEGIS_TRUSTED_SHA=$SHA bash -s" in text
    assert "aegis-pull-control.timer" in text
    assert "aegis-pull-control/state.json" in text
    assert "password" not in text.lower()
    assert "AEGIS_RUNNER_TOKEN" not in text
    assert "TS_AUTHKEY" not in text
    print("AEGIS IPHONE ASHELL BOOTSTRAP TESTS PASS")

if __name__ == "__main__":
    main()
