#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_module(state_dir: Path):
    os.environ["AEGIS_REPO_ROOT"] = str(ROOT)
    os.environ["AEGIS_PROJECT_DEFINITION"] = str(
        ROOT / "config" / "project" / "aegis-core.json"
    )
    os.environ["AEGIS_PROJECT_STATE_DIR"] = str(state_dir)
    path = ROOT / "scripts" / "aegis_project_context.py"
    spec = importlib.util.spec_from_file_location("aegis_project_context_test", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        state_dir = Path(raw) / "state"
        mod = load_module(state_dir)

        initial = mod.read_state()
        assert initial["schema"] == "aegis-project-context/v1"
        assert initial["project_id"] == "aegis-core"
        assert initial["thread_id"] == "aegis-core-main"
        assert initial["style_contract"]["language"] == "de-DE"
        assert initial["style_contract"]["voice"]["no_style_reset"] is True
        assert initial["pending_action"]
        assert initial["decisions"]
        assert initial["hard_constraints"]

        chat = mod.context_packet("chat")
        alexa = mod.context_packet("alexa")
        assert chat["project_id"] == alexa["project_id"]
        assert chat["thread_id"] == alexa["thread_id"]
        assert chat["pending_action"] == alexa["pending_action"]
        assert chat["style_fingerprint"] == alexa["style_fingerprint"]
        assert chat["hard_constraints"] == alexa["hard_constraints"]
        assert chat["render_policy"]["voice_concise"] is False
        assert alexa["render_policy"]["voice_concise"] is True
        assert alexa["render_policy"]["no_style_reset"] is True
        assert alexa["render_policy"]["preserve_pending_action"] is True

        summary = (
            "AEGIS wird am sauberen Pre-Lenovo-Punkt fortgesetzt. "
            "Local Codex/Ollama und MCP-Projektkontinuität werden auf dem UGREEN validiert."
        )
        pending = initial["pending_action"]

        to_alexa = mod.record_handoff(
            from_channel="chat",
            to_channel="alexa",
            summary=summary,
            pending_action=pending,
            actor="continuity-test-chat",
        )
        assert to_alexa["status"] == "recorded"
        alexa_state = to_alexa["context"]
        assert alexa_state["source_channel"] == "alexa"
        assert alexa_state["last_handoff_channel"] == "chat"
        assert alexa_state["project_id"] == initial["project_id"]
        assert alexa_state["thread_id"] == initial["thread_id"]
        assert alexa_state["style_fingerprint"] == initial["style_fingerprint"]
        assert alexa_state["pending_action"] == initial["pending_action"]
        assert alexa_state["decisions"] == initial["decisions"]
        assert alexa_state["blockers"] == initial["blockers"]
        assert alexa_state["turn_sequence"] == 1

        pass_alexa = mod.continuity_check(
            project_id=alexa_state["project_id"],
            thread_id=alexa_state["thread_id"],
            style_hash=alexa_state["style_fingerprint"],
            pending_action=alexa_state["pending_action"],
        )
        assert pass_alexa["status"] == "pass"
        assert all(pass_alexa["checks"].values())

        back_to_chat = mod.record_handoff(
            from_channel="alexa",
            to_channel="chat",
            summary=summary + " Alexa hat denselben Projektfaden ohne Reset weitergeführt.",
            pending_action=pending,
            actor="continuity-test-alexa",
        )
        assert back_to_chat["status"] == "recorded"
        final = back_to_chat["context"]
        assert final["source_channel"] == "chat"
        assert final["last_handoff_channel"] == "alexa"
        assert final["project_id"] == initial["project_id"]
        assert final["thread_id"] == initial["thread_id"]
        assert final["style_fingerprint"] == initial["style_fingerprint"]
        assert final["pending_action"] == initial["pending_action"]
        assert final["decisions"] == initial["decisions"]
        assert final["blockers"] == initial["blockers"]
        assert final["turn_sequence"] == 2

        pass_chat = mod.continuity_check(
            project_id=final["project_id"],
            thread_id=final["thread_id"],
            style_hash=final["style_fingerprint"],
            pending_action=final["pending_action"],
        )
        assert pass_chat["status"] == "pass"

        wrong_style = mod.continuity_check(
            project_id=final["project_id"],
            thread_id=final["thread_id"],
            style_hash="0" * 64,
            pending_action=final["pending_action"],
        )
        assert wrong_style["status"] == "blocked"
        assert wrong_style["checks"]["style_fingerprint"] is False

        wrong_thread = mod.continuity_check(
            project_id=final["project_id"],
            thread_id="new-unrelated-thread",
            style_hash=final["style_fingerprint"],
            pending_action=final["pending_action"],
        )
        assert wrong_thread["status"] == "blocked"
        assert wrong_thread["checks"]["thread_id"] is False

        wrong_pending = mod.continuity_check(
            project_id=final["project_id"],
            thread_id=final["thread_id"],
            style_hash=final["style_fingerprint"],
            pending_action="Start over from scratch",
        )
        assert wrong_pending["status"] == "blocked"
        assert wrong_pending["checks"]["pending_action"] is False

        unsupported = mod.context_packet("unknown-channel")
        assert unsupported["status"] == "blocked"

        handoffs = [
            json.loads(line)
            for line in mod.HANDOFFS_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(handoffs) == 2
        assert handoffs[0]["from_channel"] == "chat"
        assert handoffs[0]["to_channel"] == "alexa"
        assert handoffs[1]["from_channel"] == "alexa"
        assert handoffs[1]["to_channel"] == "chat"
        for row in handoffs:
            ok, reason = mod.verify_provenance(row)
            assert ok, reason
            assert row["project_id"] == initial["project_id"]
            assert row["thread_id"] == initial["thread_id"]
            assert row["style_fingerprint"] == initial["style_fingerprint"]

    print("AEGIS PROJECT CONTINUITY TESTS PASS: chat -> alexa -> chat")


if __name__ == "__main__":
    main()
