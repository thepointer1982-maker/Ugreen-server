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

        for p in [
            root / "state" / "aegis-real-cycle",
            root / "state" / "aegis-ai-miner",
            root / "state" / "aegis-guardian",
            root / "state" / "aegis-scheduler",
            root / "state" / "aegis-coder-boot",
            root / "state" / "aegis-codex-oss",
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
                    "codex_cloud": {"ready": True, "auth": "chatgpt", "automatic_fallback_allowed": False},
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
        assert status["_provenance"]["kind"] == "real-status"

    print("AEGIS REAL STATUS TESTS PASS")


if __name__ == "__main__":
    main()
