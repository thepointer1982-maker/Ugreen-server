#!/usr/bin/env python3
from __future__ import annotations
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "aegis_tailscale_bridge.sh"
COMPOSE = SCRIPT.parent.parent / "deploy" / "tailscale" / "docker-compose.yml"

def main():
    p = subprocess.run(["bash", str(SCRIPT)], text=True, capture_output=True)
    assert p.returncode == 2
    assert "no router port forwarding" in p.stdout
    text = COMPOSE.read_text(encoding="utf-8")
    assert "network_mode: host" in text
    assert "/dev/net/tun:/dev/net/tun" in text
    assert "NET_ADMIN" in text and "NET_RAW" in text
    assert "TS_AUTHKEY" not in text
    shell = SCRIPT.read_text(encoding="utf-8")
    assert "tailscale serve" in shell
    assert "tcp://127.0.0.1:22" in shell
    assert "funnel" not in shell.lower() or "no public Funnel".lower() in shell.lower()
    print("AEGIS TAILSCALE BRIDGE TESTS PASS")

if __name__ == "__main__":
    main()
