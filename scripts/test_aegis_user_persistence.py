#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "aegis_user_persistence.sh"


def main() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "loginctl enable-linger" in text
    assert "sudo -n true" in text
    assert "sudo -n loginctl enable-linger" in text
    assert '"opened_router_ports": False' in text
    assert '"stored_password": False' in text
    assert '"stored_token": False' in text
    assert "sudo loginctl" not in text.replace("sudo -n loginctl", "")
    assert "password" not in text.lower()
    print("AEGIS USER PERSISTENCE TESTS PASS")


if __name__ == "__main__":
    main()
