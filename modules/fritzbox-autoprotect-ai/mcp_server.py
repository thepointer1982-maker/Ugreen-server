from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server import MCPServer

from block0 import Engine, load_config, route_model

CFG = load_config(os.getenv("AEGIS_AUTOPROTECT_CONFIG"))
mcp = MCPServer("AEGIS FritzBox AutoProtect")


@mcp.tool()
def aegis_status() -> dict:
    """Return the last local AEGIS AutoProtect state without changing the router."""
    path = Path(CFG.state_dir) / "latest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "no-cycle-yet"}


@mcp.tool()
def aegis_fritzbox_observe() -> dict:
    """Run one read-only FRITZ!Box inventory and score cycle."""
    return Engine(CFG).once()


@mcp.tool()
def aegis_route_model(task: str = "general", memory_gb: float = 8.0) -> dict:
    """Select an installed local Ollama model for the requested task."""
    return route_model(CFG, task, memory_gb)


@mcp.tool()
def aegis_mutation_gate(score: float) -> dict:
    """Report whether autonomous configuration mutation would currently be allowed."""
    allowed, reason = Engine(CFG).mutation_gate(score)
    return {"allowed": allowed, "reason": reason}
