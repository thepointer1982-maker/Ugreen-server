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
from aegis_local_ai_miner import REPORT as AI_REPORT
from aegis_last_known_good import current, provisional_status, observe_provisional, restore

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

mcp = MCPServer(
    "AEGIS Local Guardian",
    instructions=(
        "Local-first AEGIS diagnostics. Read status, local AI evidence, "
        "model scores and learning decisions. Repair cycles are limited "
        "to the allowlist implemented by aegis_guardian_cycle.py."
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
def run_guardian_cycle(repair: bool = False) -> dict:
    """Run a measured guardian cycle with only allowlisted repairs."""
    return execute(REPO_ROOT, repair=repair)


if __name__ == "__main__":
    mcp.run()
