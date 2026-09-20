#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "aegis_shortcuts_remote_launcher.sh"

def main():
    s = SCRIPT.read_text(encoding="utf-8")
    assert "aegis-control/.aegis-control/bootstrap.json" in s
    assert 'd.get("schema") != "aegis-iphone-bootstrap/v1"' in s
    assert 'u.scheme != "https"' in s
    assert 'u.netloc != "raw.githubusercontent.com"' in s
    assert "thepointer1982-maker/Ugreen-server/{sha}/scripts/aegis_zero_cost_bootstrap.sh" in s
    assert 'AEGIS_TRUSTED_SHA="$SHA" AEGIS_DEST="$DEST" bash "$TMP_SCRIPT"' in s
    assert "aegis-pull-control/state.json" in s
    assert "aegis-coder-boot/status.json" in s
    assert "aegis-coder-boot.timer" in s
    assert "aegis-real-status/latest.json" in s
    assert "aegis_real_status.py" in s
    assert "password" not in s.lower()
    assert "AEGIS_RUNNER_TOKEN" not in s
    assert "TS_AUTHKEY" not in s
    print("AEGIS SHORTCUTS REMOTE LAUNCHER TESTS PASS")

if __name__ == "__main__":
    main()
