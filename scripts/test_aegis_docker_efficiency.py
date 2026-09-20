#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "aegis_docker_efficiency.py"

spec = importlib.util.spec_from_file_location("aegis_docker_efficiency", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def main() -> None:
    row = {
        "ID": "abc",
        "Image": "nginx:latest",
        "State": "running",
        "Names": "web",
        "Ports": "80/tcp",
        "Networks": "bridge",
        "Status": "Up 2 hours (healthy)",
    }
    assert mod.health_token(row["Status"]) == "healthy"
    assert mod.health_token("Up 5s (health: starting)") == "starting"
    assert mod.health_token("Restarting (1) 2 seconds ago") == "restarting"
    assert mod.ps_fingerprint(row) == mod.ps_fingerprint(dict(row))

    parsed = mod.parse_ps(json.dumps(row) + "\nnot-json\n")
    assert len(parsed) == 1
    assert parsed[0]["ID"] == "abc"

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        mod.STATE_DIR = root / "state"
        mod.STATUS_FILE = mod.STATE_DIR / "status.json"

        compose_root = root / "compose"
        compose_root.mkdir()
        compose = compose_root / "docker-compose.yml"
        compose.write_text("services:\n  app:\n    image: busybox\n", encoding="utf-8")

        hidden = compose_root / ".git"
        hidden.mkdir()
        (hidden / "compose.yml").write_text("bad", encoding="utf-8")
        found = mod.compose_files(compose_root)
        assert found == [compose]

        calls: list[list[str]] = []
        def fake_run(cmd: list[str], timeout: int = 30):
            import subprocess
            calls.append(cmd)
            if cmd[:3] == ["docker", "compose", "version"]:
                return subprocess.CompletedProcess(cmd, 0, "v2\n", "")
            if cmd[:3] == ["docker", "compose", "-f"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[:2] == ["docker-compose", "version"]:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        original_run = mod.run
        mod.run = fake_run
        try:
            rows1, stats1 = mod.validate_compose([compose], {})
            assert stats1["validated"] == 1
            assert stats1["cache_hits"] == 0
            assert rows1[0]["valid"] is True

            prev = {"compose": rows1}
            rows2, stats2 = mod.validate_compose([compose], prev)
            assert stats2["validated"] == 0
            assert stats2["cache_hits"] == 1
            assert rows2[0]["cache_reused"] is True
        finally:
            mod.run = original_run

        previous = {
            "last_full_inspect_at": datetime.now(timezone.utc).isoformat()
        }
        assert mod.full_inspect_due(previous) is False

        blocked = mod.blocked_report("docker-unavailable")
        assert blocked["health"] == "blocked"
        assert blocked["guardrails"]["read_only"] is True
        assert blocked["guardrails"]["docker_pull"] is False
        assert blocked["guardrails"]["docker_prune"] is False
        assert blocked["guardrails"]["docker_restart"] is False
        assert blocked["guardrails"]["registry_queries"] is False
        assert blocked["guardrails"]["volume_mutation"] is False
        ok, reason = mod.verify_provenance(blocked)
        assert ok, reason

    installer = ROOT / "scripts" / "aegis_docker_efficiency_install.sh"
    install_text = installer.read_text(encoding="utf-8")
    assert "OnBootSec=25s" in install_text
    assert "OnUnitActiveSec=2min" in install_text
    assert "AEGIS_DOCKER_FULL_INSPECT_SECONDS=900" in install_text
    assert "NoNewPrivileges=true" in install_text
    assert "ProtectSystem=full" in install_text
    assert "ReadWritePaths=$STATE_DIR" in install_text
    assert "docker pull" not in install_text
    assert "docker restart" not in install_text
    assert "docker system prune" not in install_text

    source = SCRIPT.read_text(encoding="utf-8")
    forbidden = [
        "docker pull",
        "docker prune",
        "docker system prune",
        "docker restart",
        "docker stop",
        "docker rm",
        "docker rmi",
        "docker volume rm",
    ]
    for token in forbidden:
        assert token not in source

    print("AEGIS DOCKER EFFICIENCY TESTS PASS")


if __name__ == "__main__":
    main()
