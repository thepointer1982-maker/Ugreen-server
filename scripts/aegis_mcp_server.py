#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server import MCPServer

from aegis_guardian_cycle import CARDS_FILE, STATUS_FILE, execute

REPO_ROOT = Path(os.environ.get("AEGIS_REPO_ROOT", Path(__file__).resolve().parents[1])).resolve()
mcp = MCPServer(
    "AEGIS Local Guardian",
    instructions=(
        "Local-first AEGIS diagnostics. Read status and learning cards freely. "
        "Repair cycles are limited to the allowlist implemented by aegis_guardian_cycle.py."
    ),
)


def _status() -> dict:
    if not STATUS_FILE.is_file():
        return {"status": "not-yet-measured", "hint": "Call run_guardian_cycle with repair=false first."}
    return json.loads(STATUS_FILE.read_text(encoding="utf-8"))


def _cards(limit: int = 20) -> list[dict]:
    if not CARDS_FILE.is_file():
        return []
    rows = []
    for line in CARDS_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
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


@mcp.resource("aegis://learning-cards")
def cards_resource() -> str:
    """Recent repair/diagnostic learning cards."""
    return json.dumps(_cards(50), indent=2, ensure_ascii=False)


@mcp.tool()
def guardian_status() -> dict:
    """Return the latest guardian status without changing the machine."""
    return _status()


@mcp.tool()
def learning_cards(limit: int = 20) -> list[dict]:
    """Return recent local learning cards."""
    return _cards(limit)


@mcp.tool()
def run_guardian_cycle(repair: bool = False) -> dict:
    """Run a measured guardian cycle. repair=True permits only the hard-coded reversible repair allowlist."""
    return execute(REPO_ROOT, repair=repair)


if __name__ == "__main__":
    mcp.run()
