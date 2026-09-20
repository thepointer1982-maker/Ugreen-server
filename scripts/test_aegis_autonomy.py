#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "aegis_autonomy_supervisor.py"

spec = importlib.util.spec_from_file_location("aegis_autonomy_supervisor", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def main() -> None:
    policy = json.loads(
        (ROOT / "config" / "autonomy" / "aegis-max-local.json").read_text(
            encoding="utf-8"
        )
    )
    assert policy["schema"] == "aegis-autonomy-policy/v1"
    assert policy["profile"] == "AUTO-MAX-LOCAL"
    assert policy["local_first"] is True
    assert policy["recurring_cloud_ai_cost_allowed"] is False
    assert policy["automatic_capabilities"]["select_local_model"] is True
    assert policy["automatic_capabilities"]["run_tests_and_validation"] is True
    assert policy["automatic_capabilities"]["rollback_last_known_good_on_regression"] is True
    assert policy["automatic_capabilities"]["docker_restart_only_if_explicit_autorepair_label"] is True
    assert policy["automatic_capabilities"]["audit_service_lifecycle"] is True
    assert "aegis-lifecycle.timer" in policy["allowlisted_user_units"]
    assert policy["docker_autorepair"]["required_label"] == "aegis.autorepair=true"
    for required in (
        "docker_prune",
        "delete_user_data",
        "change_secrets_or_credentials",
        "change_repository_visibility",
        "open_router_or_firewall_ports",
        "enable_paid_cloud_ai",
        "arbitrary_root_shell",
    ):
        assert required in policy["hard_blocks"]

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        old_state = mod.STATE_DIR
        old_status = mod.STATUS_FILE
        old_queue = mod.QUEUE_FILE
        old_log = mod.ACTION_LOG
        old_docker = mod.DOCKER_STATE
        old_router = mod.CODER_ROUTER
        try:
            mod.STATE_DIR = root / "state"
            mod.STATUS_FILE = mod.STATE_DIR / "status.json"
            mod.QUEUE_FILE = mod.STATE_DIR / "tasks.jsonl"
            mod.ACTION_LOG = mod.STATE_DIR / "actions.jsonl"
            mod.DOCKER_STATE = root / "docker.json"

            queued = mod.queue_task(
                "Continue the current AEGIS project and validate only local changes.",
                source="test",
                priority="high",
            )
            assert queued["status"] == "queued"
            rows = mod.load_queue()
            assert len(rows) == 1
            assert rows[0]["priority"] == "high"
            assert rows[0]["status"] == "queued"

            assert mod.queue_task("", source="test")["status"] == "blocked"
            assert mod.queue_task("x", priority="invalid")["status"] == "blocked"

            captured_units: list[str] = []
            original_state_fn = mod.systemd_user_state
            original_run = mod.run
            mod.systemd_user_state = lambda unit: {
                "load": "loaded",
                "active": "inactive" if unit == "aegis-export.timer" else "active",
            }
            def fake_run(cmd, **kwargs):
                if cmd[:3] == ["systemctl", "--user", "start"]:
                    captured_units.append(cmd[3])
                return subprocess.CompletedProcess(cmd, 0, "{}", "")
            mod.run = fake_run
            actions, used = mod.ensure_user_units(policy, 4)
            assert used == 1
            assert captured_units == ["aegis-export.timer"]
            assert actions[0]["ok"] is True
            mod.systemd_user_state = original_state_fn
            mod.run = original_run

            docker_payload = mod.attach_provenance(
                {
                    "schema": "aegis-docker-efficiency/v1",
                    "health": "degraded",
                    "containers": [
                        {
                            "id": "abc123",
                            "name": "opted-in",
                            "ps_health": "unhealthy",
                            "details": {"health": "unhealthy"},
                        },
                        {
                            "id": "def456",
                            "name": "healthy",
                            "ps_health": "healthy",
                            "details": {"health": "healthy"},
                        },
                    ],
                },
                kind="docker-efficiency",
            )
            mod.DOCKER_STATE.write_text(
                json.dumps(docker_payload),
                encoding="utf-8",
            )
            previous = {
                "docker_restart_state": {
                    "abc123": {
                        "consecutive_bad": 2,
                        "last_restart_epoch": 0.0,
                        "restart_epochs": [],
                    }
                }
            }
            original_label = mod._docker_label_allows
            original_run = mod.run
            mod._docker_label_allows = lambda cid, required: (
                cid == "abc123" and required == "aegis.autorepair=true"
            )
            docker_calls: list[list[str]] = []
            def docker_run(cmd, **kwargs):
                docker_calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            mod.run = docker_run
            repairs, restart_state = mod.maybe_restart_opted_in_docker(
                policy,
                previous,
                2,
            )
            assert len(repairs) == 1
            assert repairs[0]["action"] == "restart-opted-in-docker"
            assert repairs[0]["target"] == "abc123"
            assert ["docker", "restart", "--time", "10", "abc123"] in docker_calls
            assert restart_state["abc123"]["consecutive_bad"] == 0
            mod._docker_label_allows = original_label
            mod.run = original_run

            fake_router = root / "router.sh"
            fake_router.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            fake_router.chmod(0o755)
            mod.CODER_ROUTER = fake_router
            original_run = mod.run
            def ai_run(cmd, **kwargs):
                return subprocess.CompletedProcess(cmd, 0, '{"mode":"codex-local"}', "")
            mod.run = ai_run
            result = mod.process_one_local_ai_task(policy)
            assert result is not None
            assert result["ok"] is True
            rows = mod.load_queue()
            assert rows[0]["status"] == "completed"
            mod.run = original_run

            breaker_policy = dict(policy)
            breaker_policy["cycle"] = dict(policy["cycle"])
            breaker_policy["cycle"]["max_consecutive_failed_cycles"] = 3
            breaker_policy["cycle"]["circuit_breaker_seconds"] = 900
            is_open, remaining = mod.circuit_open(
                {
                    "consecutive_failed_cycles": 3,
                    "circuit_opened_epoch": mod.time.time(),
                },
                breaker_policy,
            )
            assert is_open is True
            assert remaining > 0
        finally:
            mod.STATE_DIR = old_state
            mod.STATUS_FILE = old_status
            mod.QUEUE_FILE = old_queue
            mod.ACTION_LOG = old_log
            mod.DOCKER_STATE = old_docker
            mod.CODER_ROUTER = old_router

    installer = (
        ROOT / "scripts" / "aegis_autonomy_install.sh"
    ).read_text(encoding="utf-8")
    assert "OnUnitActiveSec=2min" in installer
    assert "AEGIS_ALLOW_CLOUD_CODEX=0" in installer
    assert "AEGIS_PROJECT_CONTEXT_REQUIRED=1" in installer
    assert "NoNewPrivileges=true" in installer
    assert "ProtectSystem=full" in installer
    assert "sudo" not in installer

    source = SCRIPT.read_text(encoding="utf-8")
    assert "docker prune" not in source
    assert "docker system prune" not in source
    assert "docker pull" not in source
    assert "sudo" not in source
    assert "AEGIS_ALLOW_CLOUD_CODEX" in source

    print("AEGIS AUTO-MAX-LOCAL TESTS PASS")


if __name__ == "__main__":
    main()
