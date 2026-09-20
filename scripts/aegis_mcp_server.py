#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from mcp.server import MCPServer

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aegis_guardian_cycle import CARDS_FILE, STATUS_FILE, execute
from aegis_autonomy_supervisor import (
    POLICY_FILE as AUTONOMY_POLICY_FILE,
    STATUS_FILE as AUTONOMY_STATUS_FILE,
    queue_task as autonomy_queue_task,
    run_cycle as autonomy_run_cycle,
)
from aegis_local_ai_miner import REPORT as AI_REPORT
from aegis_last_known_good import active_status, activate_current_lkg, current, provisional_status, observe_provisional, restore
from aegis_provenance import verify_provenance
from aegis_project_context import (
    context_packet as project_context_packet_impl,
    continuity_check as project_continuity_check_impl,
    read_state as project_context_impl,
    record_handoff as record_project_handoff_impl,
)

REPO_ROOT = Path(
    os.environ.get("AEGIS_REPO_ROOT", Path(__file__).resolve().parents[1])
).resolve()
MATRIX_FILE = Path(
    os.environ.get(
        "AEGIS_MODEL_MATRIX",
        Path.home() / ".local/state/aegis-ai-miner/model-agent-matrix.json",
    )
)
LEARNING_GATE_FILE = Path(
    os.environ.get(
        "AEGIS_LEARNING_GATE",
        Path.home() / ".local/state/aegis-ai-miner/learning-gate.json",
    )
)
PULL_CONTROL_FILE = Path(
    os.environ.get(
        "AEGIS_PULL_CONTROL_STATE_FILE",
        Path.home() / ".local/state/aegis-pull-control/state.json",
    )
)

mcp = MCPServer(
    "AEGIS Local Guardian",
    instructions=(
        "Local-first AEGIS diagnostics. Read status, local AI evidence, "
        "model scores, learning decisions, signed project continuity state, and "
        "the shared AUTO-MAX-LOCAL autonomy policy. Channel handoffs preserve "
        "project/thread/style/pending-action identity. Autonomous work remains local, "
        "reversible and policy-gated; destructive/external effects remain blocked."
    ),
)


def _read_json(path: Path, missing_status: str) -> dict:
    if not path.is_file():
        return {"status": missing_status}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception as exc:
        return {"status": "unreadable", "error": f"{type(exc).__name__}: {exc}"}


def _verified_state(path: Path, missing_status: str) -> dict:
    value = _read_json(path, missing_status)
    if value.get("status") == missing_status:
        return value
    ok, reason = verify_provenance(value)
    if not ok:
        return {
            "status": "blocked",
            "reason": "provenance-invalid",
            "provenance_reason": reason,
        }
    result = dict(value)
    result["provenance_verified"] = True
    return result


def _status() -> dict:
    if not STATUS_FILE.is_file():
        return {
            "status": "not-yet-measured",
            "hint": "Call run_guardian_cycle with repair=false first.",
        }
    return _read_json(STATUS_FILE, "not-yet-measured")


def _ai_report() -> dict:
    if not AI_REPORT.is_file():
        return {
            "status": "not-yet-mined",
            "hint": "Run scripts/aegis_local_ai_miner.py locally first.",
        }
    return _read_json(AI_REPORT, "not-yet-mined")


def _cards(limit: int = 20) -> list[dict]:
    if not CARDS_FILE.is_file():
        return []
    rows: list[dict] = []
    for line in CARDS_FILE.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        except json.JSONDecodeError:
            continue
    return rows[-max(1, min(limit, 200)):]


@mcp.resource("aegis://status")
def status_resource() -> str:
    """Latest verified local AEGIS guardian status."""
    return json.dumps(_status(), indent=2, ensure_ascii=False)


@mcp.resource("aegis://local-ai")
def ai_resource() -> str:
    """Latest read-only local AI/protocol inventory."""
    return json.dumps(_ai_report(), indent=2, ensure_ascii=False)


@mcp.resource("aegis://model-matrix")
def model_matrix_resource() -> str:
    """Latest local model/agent score matrix."""
    return json.dumps(
        _read_json(MATRIX_FILE, "not-yet-built"),
        indent=2,
        ensure_ascii=False,
    )


@mcp.resource("aegis://learning-gate")
def learning_gate_resource() -> str:
    """Latest fail-closed learning acceptance decision."""
    return json.dumps(
        _read_json(LEARNING_GATE_FILE, "not-yet-evaluated"),
        indent=2,
        ensure_ascii=False,
    )


@mcp.resource("aegis://learning-cards")
def cards_resource() -> str:
    """Recent repair/diagnostic learning cards."""
    return json.dumps(_cards(50), indent=2, ensure_ascii=False)


@mcp.resource("aegis://project-context")
def project_context_resource() -> str:
    """Signed model-independent AEGIS project/dialog continuity state."""
    return json.dumps(project_context_impl(), indent=2, ensure_ascii=False)


@mcp.resource("aegis://project-context/alexa")
def alexa_context_resource() -> str:
    """Alexa/voice rendering packet for the active AEGIS thread."""
    return json.dumps(
        project_context_packet_impl("alexa"),
        indent=2,
        ensure_ascii=False,
    )


@mcp.resource("aegis://autonomy")
def autonomy_resource() -> str:
    """Latest AUTO-MAX-LOCAL supervisor state."""
    return json.dumps(
        _read_json(AUTONOMY_STATUS_FILE, "not-yet-measured"),
        indent=2,
        ensure_ascii=False,
    )


@mcp.resource("aegis://autonomy/policy")
def autonomy_policy_resource() -> str:
    """Shared maximum-safe-local autonomy policy."""
    return json.dumps(
        _read_json(AUTONOMY_POLICY_FILE, "missing"),
        indent=2,
        ensure_ascii=False,
    )


@mcp.resource("aegis://control/primary")
def primary_control_resource() -> str:
    """Verified NAS primary outbound-pull control state."""
    return json.dumps(
        _verified_state(PULL_CONTROL_FILE, "not-yet-measured"),
        indent=2,
        ensure_ascii=False,
    )


@mcp.tool()
def guardian_status() -> dict:
    """Return the latest guardian status without changing the machine."""
    return _status()


@mcp.tool()
def local_ai_inventory() -> dict:
    """Return the latest local AI/protocol evidence report."""
    return _ai_report()


@mcp.tool()
def model_agent_matrix() -> dict:
    """Return the latest local model/agent score matrix."""
    return _read_json(MATRIX_FILE, "not-yet-built")


@mcp.tool()
def learning_gate_status() -> dict:
    """Return the latest fail-closed learning acceptance decision."""
    return _read_json(LEARNING_GATE_FILE, "not-yet-evaluated")


@mcp.tool()
def active_state_status() -> dict:
    """Return the verified active-state validation status."""
    return active_status()


@mcp.tool()
def activate_last_known_good() -> dict:
    """Activate the verified LKG at the operator-configured local target."""
    raw_target = os.environ.get("AEGIS_LKG_ACTIVATION_TARGET")
    if not raw_target:
        return {
            "status": "blocked",
            "reason": "activation-target-not-configured",
        }
    return activate_current_lkg(Path(raw_target).expanduser())


@mcp.tool()
def provisional_learning_status() -> dict:
    """Return the current provisional candidate and probation counters."""
    return provisional_status()


@mcp.tool()
def record_probation_result(passed: bool) -> dict:
    """Record one measured probation result for the provisional candidate."""
    raw_target = os.environ.get("AEGIS_LKG_MCP_TARGET")
    rollback_target = Path(raw_target).expanduser() if raw_target else None
    return observe_provisional(
        passed=passed,
        evidence={"source": "mcp"},
        rollback_destination=rollback_target,
    )


@mcp.tool()
def last_known_good_status() -> dict:
    """Return the verified last-known-good pointer and artifact status."""
    return current()


@mcp.tool()
def restore_last_known_good() -> dict:
    """Restore the verified last-known-good artifact to the operator-configured local target."""
    raw_target = os.environ.get("AEGIS_LKG_MCP_TARGET")
    if not raw_target:
        return {
            "status": "blocked",
            "reason": "mcp-rollback-target-not-configured",
        }
    return restore(Path(raw_target).expanduser())


@mcp.tool()
def learning_cards(limit: int = 20) -> list[dict]:
    """Return recent local learning cards."""
    return _cards(limit)


@mcp.tool()
def project_context() -> dict:
    """Return the signed active project/thread/style/pending-action context."""
    return project_context_impl()


@mcp.tool()
def primary_control_status() -> dict:
    """Return verified NAS outbound-pull sequence/transport state."""
    return _verified_state(PULL_CONTROL_FILE, "not-yet-measured")


@mcp.tool()
def autonomy_status() -> dict:
    """Return the latest AUTO-MAX-LOCAL supervisor state."""
    return _read_json(AUTONOMY_STATUS_FILE, "not-yet-measured")


@mcp.tool()
def autonomy_policy() -> dict:
    """Return the shared maximum-safe-local autonomy policy."""
    return _read_json(AUTONOMY_POLICY_FILE, "missing")


@mcp.tool()
def enqueue_autonomy_task(
    task: str,
    priority: str = "normal",
    source: str = "mcp",
) -> dict:
    """Queue one bounded local-AI task for autonomous processing."""
    return autonomy_queue_task(
        task,
        source=source,
        priority=priority,
    )


@mcp.tool()
def run_autonomy_cycle() -> dict:
    """Run one local policy-gated AUTO-MAX-LOCAL supervisor cycle."""
    return autonomy_run_cycle()


@mcp.tool()
def project_context_packet(channel: str = "chat") -> dict:
    """Return a channel-specific context packet without changing project state."""
    return project_context_packet_impl(channel)


@mcp.tool()
def project_continuity_check(
    project_id: str,
    thread_id: str,
    style_fingerprint: str,
    pending_action: str | None = None,
) -> dict:
    """Fail closed if a model/channel has drifted from the active project context."""
    return project_continuity_check_impl(
        project_id=project_id,
        thread_id=thread_id,
        style_hash=style_fingerprint,
        pending_action=pending_action,
    )


@mcp.tool()
def record_project_handoff(
    from_channel: str,
    to_channel: str,
    summary: str,
    pending_action: str,
    decisions: list[str] | None = None,
    blockers: list[str] | None = None,
    actor: str = "mcp",
) -> dict:
    """Record a bounded structured channel/model handoff; no full transcript is stored."""
    return record_project_handoff_impl(
        from_channel=from_channel,
        to_channel=to_channel,
        summary=summary,
        pending_action=pending_action,
        decisions=decisions,
        blockers=blockers,
        actor=actor,
    )


@mcp.tool()
def run_guardian_cycle(repair: bool = False) -> dict:
    """Run a measured guardian cycle with only allowlisted repairs."""
    return execute(REPO_ROOT, repair=repair)


if __name__ == "__main__":
    mcp.run()
