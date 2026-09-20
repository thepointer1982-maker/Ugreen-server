from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from engine import EVIDENCE_CLASSES, REAL_EVIDENCE, engine_from_env

mcp = MCPServer("AEGIS Score Evidence MCP")


@mcp.tool()
def aegis_score_sources() -> dict[str, Any]:
    """Return configured AEGIS score-source availability without exposing raw logs."""
    return engine_from_env().source_status()


@mcp.tool()
def aegis_score_changes(include_unchanged: bool = False) -> dict[str, Any]:
    """Scan AEGIS score artifacts and compare them with the last committed sanitized baseline.

    This tool is read-only for GitHub clones, Drive mounts, NAS logs, router state and devices.
    Non-real evidence is never counted as a real improvement.
    """
    return engine_from_env().scan(include_unchanged=include_unchanged)


@mcp.tool()
def aegis_score_commit_baseline() -> dict[str, Any]:
    """Persist the currently scanned, sanitized score/status metadata as the next comparison baseline.

    Only the MCP state directory is written. Source repositories, Drive data, NAS configuration,
    router/firmware/account settings and raw diagnostic logs are never modified.
    """
    return engine_from_env().commit_baseline()


@mcp.resource("aegis://scores/evidence-policy")
def evidence_policy() -> dict[str, Any]:
    """Expose the immutable evidence policy used by the score scanner."""
    return {
        "evidence_classes": sorted(EVIDENCE_CLASSES),
        "real_evidence_classes": sorted(REAL_EVIDENCE),
        "rules": [
            "TEST_FIXTURE, SYNTHETIC, HEURISTIC, PROJECTED_TARGET, TEMPLATE and ARCHIVE_CLAIM never count as real improvement.",
            "VERIFIED_EXPORT requires a matching manifest hash and export-session identifier.",
            "REAL_DEVICE_MEASUREMENT requires explicit classification plus device/observation metadata.",
            "A score with stale, future-dated or hash-mismatched evidence cannot count as a verified real improvement.",
            "Non-real score changes do not trigger a notification.",
            "No raw private logs, credentials, tokens or unrestricted device data are returned by the MCP tools.",
        ],
    }


if __name__ == "__main__":
    mcp.run()
