from __future__ import annotations

import os

from mcp.server import MCPServer

from block0 import Engine, load_config, public_summary, route_model

CFG = load_config(os.getenv("AEGIS_AUTOPROTECT_CONFIG"))
mcp = MCPServer("AEGIS AutoProtect")


def _engine() -> Engine:
    return Engine(CFG)


@mcp.tool()
def aegis_status() -> dict:
    """Return a privacy-safe summary of the latest local AutoProtect cycle."""
    latest = _engine().latest()
    return public_summary(latest) if latest else {"status": "no-cycle-yet"}


@mcp.tool()
def aegis_fritzbox_observe() -> dict:
    """Run one read-only FRITZ!Box cycle and return only a privacy-safe summary."""
    return public_summary(_engine().once())


@mcp.tool()
def aegis_route_model(task: str = "general", memory_gb: float = 8.0) -> dict:
    """Select an installed model; production defaults to local Ollama only."""
    return route_model(CFG, task, memory_gb)


@mcp.tool()
def aegis_mutation_gate() -> dict:
    """Report the gate derived from the latest verified local cycle; no caller score is accepted."""
    engine = _engine()
    allowed, reason = engine.current_mutation_gate()
    latest = engine.latest()
    summary = public_summary(latest) if latest else {}
    return {
        "allowed": allowed,
        "reason": reason,
        "score": summary.get("score"),
        "gate_passed": summary.get("gate_passed", False),
    }


@mcp.tool()
def aegis_self_check() -> dict:
    """Check local state permissions, audit integrity and safe-mode configuration."""
    return _engine().self_check()
