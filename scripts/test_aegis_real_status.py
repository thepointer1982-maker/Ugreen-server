#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT = SCRIPT_DIR / "aegis_real_status.py"
spec = importlib.util.spec_from_file_location("aegis_real_status", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

from aegis_provenance import attach_provenance


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        os.environ["XDG_STATE_HOME"] = str(root / "state")
        os.environ["AEGIS_REAL_CYCLE_STATE_DIR"] = str(root / "state" / "aegis-real-cycle")
        os.environ["AEGIS_AI_MINER_STATE_DIR"] = str(root / "state" / "aegis-ai-miner")
        os.environ["AEGIS_GUARDIAN_STATE_DIR"] = str(root / "state" / "aegis-guardian")
        os.environ["AEGIS_SCHEDULER_STATE_DIR"] = str(root / "state" / "aegis-scheduler")
        os.environ["AEGIS_LKG_STATE_DIR"] = str(root / "state" / "aegis-last-known-good")
        os.environ["AEGIS_CODER_BOOT_STATE_FILE"] = str(root / "state" / "aegis-coder-boot" / "status.json")
        os.environ["AEGIS_CODEX_OSS_STATE_FILE"] = str(root / "state" / "aegis-codex-oss" / "status.json")
        os.environ["AEGIS_MCP_STATE_FILE"] = str(root / "state" / "aegis-mcp" / "runtime.json")
        os.environ["AEGIS_RUNNER_STATE_FILE"] = str(root / "state" / "aegis-runner" / "status.json")
        os.environ["AEGIS_DOCKER_EFFICIENCY_STATE_FILE"] = str(
            root / "state" / "aegis-docker-efficiency" / "status.json"
        )
        os.environ["AEGIS_AUTONOMY_STATUS_FILE"] = str(
            root / "state" / "aegis-autonomy" / "status.json"
        )
        os.environ["AEGIS_PULL_CONTROL_STATE_FILE"] = str(
            root / "state" / "aegis-pull-control" / "state.json"
        )

        for p in [
            root / "state" / "aegis-real-cycle",
            root / "state" / "aegis-ai-miner",
            root / "state" / "aegis-guardian",
            root / "state" / "aegis-scheduler",
            root / "state" / "aegis-coder-boot",
            root / "state" / "aegis-codex-oss",
            root / "state" / "aegis-mcp",
            root / "state" / "aegis-runner",
            root / "state" / "aegis-docker-efficiency",
            root / "state" / "aegis-autonomy",
            root / "state" / "aegis-pull-control",
        ]:
            p.mkdir(parents=True, exist_ok=True)

        real = attach_provenance(
            {
                "status": "healthy",
                "reason": "verified-real-cycle",
                "generated_at": "2026-09-19T00:00:00Z",
                "sources": {},
                "matrix": {"status": "skipped"},
            },
            kind="real-cycle",
        )
        ai = attach_provenance(
            {"inventory": {}, "ollama": {"reachable": False}},
            kind="local-ai-miner",
        )
        (root / "state" / "aegis-real-cycle" / "latest.json").write_text(
            json.dumps(real), encoding="utf-8"
        )
        (root / "state" / "aegis-ai-miner" / "latest.json").write_text(
            json.dumps(ai), encoding="utf-8"
        )
        (root / "state" / "aegis-guardian" / "status.json").write_text(
            json.dumps(
                {
                    "mode": "healthy",
                    "reason": "verified-cycle",
                    "evidence": {
                        "network_score": 80,
                        "deepdiag_score": 90,
                        "evidence_verified": True,
                    },
                }
            ),
            encoding="utf-8",
        )
        (root / "state" / "aegis-scheduler" / "state.env").write_text(
            "failures=0\nnext_allowed=0\n",
            encoding="utf-8",
        )
        (root / "state" / "aegis-coder-boot" / "status.json").write_text(
            json.dumps(
                {
                    "schema": "aegis-coder-boot/v2",
                    "health": "healthy",
                    "reason": "codex-oss-ollama-ready",
                    "selected": "codex-local",
                    "codex_local": {"ready": True, "provider": "ollama"},
                    "codex_cloud": {"ready": False, "auth": "skipped", "automatic_fallback_allowed": False},
                    "opencode_local": {"ready": True},
                }
            ),
            encoding="utf-8",
        )

        (root / "state" / "aegis-codex-oss" / "status.json").write_text(
            json.dumps(
                {
                    "schema": "aegis-codex-oss/v1",
                    "health": "healthy",
                    "reason": "codex-oss-ollama-ready",
                    "provider": "ollama",
                    "model": "qwen2.5-coder:7b",
                    "ollama_ready": True,
                    "model_ready": True,
                    "cloud_model_usage": False,
                    "chatgpt_auth_required": False,
                    "openai_api_key_required": False,
                    "web_search": "disabled",
                    "shell_network_access": False,
                }
            ),
            encoding="utf-8",
        )

        (root / "state" / "aegis-mcp" / "runtime.json").write_text(
            json.dumps(
                {
                    "schema": "aegis-mcp-runtime/v1",
                    "health": "healthy",
                    "reason": "mcp-runtime-ready",
                    "transport": "stdio",
                    "network_listener": False,
                    "public_port": False,
                    "wrapper": "/home/aegis/.local/bin/aegis-mcp-local",
                }
            ),
            encoding="utf-8",
        )
        (root / "state" / "aegis-runner" / "status.json").write_text(
            json.dumps(
                {
                    "schema": "aegis-runner/v2",
                    "configured": True,
                    "service_mode": "user-systemd",
                    "service_active": False,
                    "runner_name": "aegis-ugreen-v2",
                    "labels": ["aegis-ugreen-v2"],
                }
            ),
            encoding="utf-8",
        )

        docker_state = attach_provenance(
            {
                "schema": "aegis-docker-efficiency/v1",
                "health": "healthy",
                "reason": "stable",
                "full_inspect_performed": False,
                "metrics": {
                    "container_count": 6,
                    "container_cache_hits": 6,
                    "inspect_calls": 0,
                    "compose_files": 2,
                    "compose_cache_hits": 2,
                },
                "guardrails": {
                    "read_only": True,
                    "docker_pull": False,
                    "docker_prune": False,
                    "docker_restart": False,
                    "registry_queries": False,
                },
            },
            kind="docker-efficiency",
        )
        (root / "state" / "aegis-docker-efficiency" / "status.json").write_text(
            json.dumps(docker_state),
            encoding="utf-8",
        )

        autonomy_state = attach_provenance(
            {
                "schema": "aegis-autonomy-status/v1",
                "profile": "AUTO-MAX-LOCAL",
                "health": "healthy",
                "reason": "cycle-complete",
                "action_count": 1,
                "queue": {"queued": 0, "processed_task": None},
                "consecutive_failed_cycles": 0,
                "policy": {
                    "profile": "AUTO-MAX-LOCAL",
                    "local_first": True,
                    "recurring_cloud_ai_cost_allowed": False,
                },
            },
            kind="autonomy-status",
        )
        (root / "state" / "aegis-autonomy" / "status.json").write_text(
            json.dumps(autonomy_state),
            encoding="utf-8",
        )

        pull_state = attach_provenance(
            {
                "status": "success",
                "transport": "outbound-pull",
                "runner_required": False,
                "action": "status",
                "sequence": 19,
                "seen_sequence": 19,
                "last_sequence": 19,
                "trusted_sha": "0" * 40,
                "control_head": "1" * 40,
                "returncode": 0,
                "completed_at": "2026-09-20T16:44:00+00:00",
            },
            kind="pull-control-state",
        )
        (root / "state" / "aegis-pull-control" / "state.json").write_text(
            json.dumps(pull_state),
            encoding="utf-8",
        )

        mod.systemd_status = lambda unit: {
            "available": True,
            "scope": "user",
            "LoadState": "loaded",
            "ActiveState": "active" if unit.endswith(".timer") else "inactive",
            "SubState": "waiting" if unit.endswith(".timer") else "dead",
            "Result": "success",
            "ExecMainStatus": "0",
        }
        mod.ollama_status = lambda: {"reachable": True, "models": ["qwen2.5-coder:7b"]}

        status = mod.build_status(root)
        assert status["health"] == "healthy"
        assert status["guardian"]["network_score"] == 80
        assert status["ollama"]["model_count"] == 1
        assert status["scheduler"]["timer"]["ActiveState"] == "active"
        assert status["coder"]["capability"] == "ready"
        assert status["coder"]["selected"] == "codex-local"
        assert status["coder"]["codex_oss"]["health"] == "healthy"
        assert status["coder"]["codex_oss"]["provider"] == "ollama"
        assert status["coder"]["codex_oss"]["cloud_model_usage"] is False
        assert status["coder"]["codex_oss"]["openai_api_key_required"] is False
        assert status["coder"]["codex_oss"]["web_search"] == "disabled"
        assert status["control"]["mcp"]["health"] == "healthy"
        assert status["control"]["mcp"]["transport"] == "stdio"
        assert status["control"]["mcp"]["network_listener"] is False
        assert status["control"]["mcp"]["public_port"] is False
        assert status["control"]["runner"]["configured"] is True
        assert status["control"]["runner"]["service_active"] is False
        assert status["control"]["runner"]["optional_fallback"] is True
        assert status["control"]["primary"]["transport"] == "outbound-pull"
        assert status["control"]["primary"]["ready"] is True
        assert status["control"]["primary"]["runner_required"] is False
        assert status["control"]["pull_control"]["provenance_verified"] is True
        assert status["control"]["pull_control"]["last_sequence"] == 19
        assert status["scheduler"]["pull_control_timer"]["ActiveState"] == "active"
        assert "runner-service-inactive" not in status["blockers"]
        assert status["control"]["runner"]["runner_name"] == "aegis-ugreen-v2"
        assert status["control"]["docker"]["health"] == "healthy"
        assert status["control"]["docker"]["metrics"]["container_count"] == 6
        assert status["control"]["docker"]["metrics"]["inspect_calls"] == 0
        assert status["control"]["docker"]["guardrails"]["docker_pull"] is False
        assert status["scheduler"]["docker_efficiency_timer"]["ActiveState"] == "active"
        assert status["scheduler"]["autonomy_timer"]["ActiveState"] == "active"
        assert status["control"]["autonomy"]["profile"] == "AUTO-MAX-LOCAL"
        assert status["control"]["autonomy"]["health"] == "healthy"
        assert status["control"]["autonomy"]["queue"]["queued"] == 0
        assert status["control"]["autonomy"]["policy"]["recurring_cloud_ai_cost_allowed"] is False
        assert status["_provenance"]["kind"] == "real-status"

    print("AEGIS REAL STATUS TESTS PASS")


if __name__ == "__main__":
    main()
