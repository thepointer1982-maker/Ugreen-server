from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from engine import EVIDENCE_CLASSES, REAL_EVIDENCE, engine_from_env

mcp = MCPServer("AEGIS Score Bus")


_SOURCE_ALIASES = {
    "ugreen": "ugreen-server",
    "openjarvis": "openjarvis-main",
    "drive": "drive-datahub",
}


def _source_root(source: str) -> Path:
    engine = engine_from_env()
    canonical = _SOURCE_ALIASES.get(source)
    if canonical is None or canonical not in engine.roots:
        raise ValueError("Unknown AEGIS evidence source")
    return engine.roots[canonical].resolve()


def _inside(root: Path, rel: str) -> Path:
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path escapes configured AEGIS evidence root") from exc
    return candidate


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_manifest(root: Path, rel_manifest: str) -> dict[str, Any]:
    manifest = _inside(root, rel_manifest)
    if not manifest.is_file():
        raise FileNotFoundError(rel_manifest)

    checks: list[dict[str, Any]] = []
    for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        match = re.match(r"^([0-9a-fA-F]{64})\s+[* ]?(.+)$", text)
        if not match:
            checks.append({"line": text, "valid": False, "reason": "unparsed"})
            continue
        expected, rel = match.groups()
        candidate = (manifest.parent / rel.strip()).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            checks.append({"file": rel.strip(), "valid": False, "reason": "path-escape"})
            continue
        if not candidate.is_file():
            checks.append({"file": str(candidate.relative_to(root)), "valid": False, "reason": "missing"})
            continue
        actual = _file_sha256(candidate)
        checks.append({
            "file": str(candidate.relative_to(root)),
            "expected_sha256": expected.lower(),
            "actual_sha256": actual,
            "valid": actual == expected.lower(),
        })

    return {
        "manifest": rel_manifest,
        "verified": bool(checks) and all(item.get("valid") is True for item in checks),
        "checks": checks,
    }


@mcp.tool()
def aegis_sources_status() -> dict[str, Any]:
    """Return configured AEGIS score-source availability without exposing raw logs."""
    return engine_from_env().source_status()


@mcp.tool()
def aegis_score_snapshot(
    include_unchanged: bool = False,
    commit_baseline: bool = True,
    initialize_baseline: bool = False,
) -> dict[str, Any]:
    """Compare all canonical AEGIS score evidence with the last sanitized baseline.

    initialize_baseline=True is for the first activation after a separately verified checkpoint:
    it records the current sanitized state and suppresses duplicate "new" notifications.
    """
    engine = engine_from_env()

    if initialize_baseline:
        preflight = engine.scan(include_unchanged=True)
        integrity_alerts = [
            item for item in preflight.get("alerts", [])
            if any(
                flag in (item.get("warning") or "")
                for flag in ("HASH_MISMATCH", "FUTURE_TIMESTAMP")
            )
        ]
        if integrity_alerts or preflight.get("conflict_count", 0):
            preflight["baseline_initialized"] = False
            preflight["baseline_init_blocked"] = True
            preflight["baseline_init_block_reasons"] = {
                "integrity_alerts": integrity_alerts,
                "conflicts": preflight.get("conflicts", []),
            }
            preflight["notification_recommended"] = True
            return preflight

        baseline = engine.commit_baseline()
        result = engine.scan(include_unchanged=include_unchanged)
        result["baseline_initialized"] = True
        result["baseline_init_blocked"] = False
        result["baseline_commit"] = baseline
        result["notification_recommended"] = False
        return result

    result = engine.scan(include_unchanged=include_unchanged)
    result["baseline_initialized"] = False
    if commit_baseline:
        result["baseline_commit"] = engine.commit_baseline()
    return result


@mcp.tool()
def aegis_verify_manifest(source: str, manifest: str) -> dict[str, Any]:
    """Verify a SHA-256 manifest under one configured AEGIS evidence root."""
    result = _verify_manifest(_source_root(source), manifest)
    result["source"] = source
    return result


@mcp.tool()
def aegis_evidence_read(source: str, path: str) -> dict[str, Any]:
    """Return bounded evidence metadata only; never return raw log/file contents."""
    root = _source_root(source)
    candidate = _inside(root, path)
    if not candidate.is_file():
        raise FileNotFoundError(path)
    if candidate.stat().st_size > 1024 * 1024:
        raise ValueError("Evidence file exceeds 1 MiB metadata-inspection limit")

    suffix = candidate.suffix.lower()
    allowed = {".json", ".jsonl", ".md", ".txt", ".sha256", ".yaml", ".yml"}
    if suffix not in allowed:
        raise ValueError("Raw logs, binary files, or unsupported evidence types are not publishable")

    info: dict[str, Any] = {
        "source": source,
        "path": str(candidate.relative_to(root)),
        "bytes": candidate.stat().st_size,
        "modified_at": datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
        "sha256": _file_sha256(candidate),
        "raw_content_returned": False,
    }

    sibling_manifest = candidate.parent / "manifest.sha256"
    if sibling_manifest.is_file():
        verification = _verify_manifest(root, str(sibling_manifest.relative_to(root)))
        matching = [
            item for item in verification["checks"]
            if item.get("file") == str(candidate.relative_to(root))
        ]
        info["hash_verified"] = matching[0].get("valid") if matching else None
    else:
        info["hash_verified"] = None
    return info


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
            "Stale, future-dated or hash-mismatched evidence cannot support a current live claim.",
            "Non-real score changes do not trigger a notification.",
            "Conflicting current verified scores and disappearance of previously verified evidence are reportable.",
            "Raw private logs, credentials, tokens and unrestricted device data are never returned.",
        ],
    }


if __name__ == "__main__":
    mcp.run()
