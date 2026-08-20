#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
SCORES = ROOT / "scores"
FIXES = ROOT / "fixes" / "done"
DASH = ROOT / "dashboard"
GENERATED_SCORE_FILES = {"latest.json", "deepdiag.json", "history.jsonl", "export-session.json"}


def load_json(path: Path):
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def safe_float(value, default=0.0) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def parse_ts(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def priority(score: float, reachable: bool, anomaly: float) -> str:
    if not reachable or score < 40 or anomaly >= 90:
        return "P0"
    if score < 60 or anomaly >= 70:
        return "P1"
    if score < 80 or anomaly >= 50:
        return "P2"
    return "P3"


def verify_export(latest: dict) -> tuple[bool, str, dict | None]:
    session_p = SCORES / "export-session.json"
    manifest_p = SCORES / "manifest.sha256"
    if not session_p.exists() or not manifest_p.exists():
        return False, "export_session_or_manifest_missing", None
    try:
        session = load_json(session_p)
    except Exception:
        return False, "export_session_invalid_json", None
    if not isinstance(session, dict) or session.get("schema_version") != 2:
        return False, "export_session_invalid", None

    listed = session.get("files")
    if not isinstance(listed, list) or not all(isinstance(x, str) for x in listed):
        return False, "export_session_files_invalid", None
    expected_session_files = set(listed) | {"export-session.json"}
    actual = {p.name for p in SCORES.iterdir() if p.is_file() and p.name in GENERATED_SCORE_FILES}
    if actual != expected_session_files:
        return False, "export_session_file_set_mismatch", None

    entries: dict[str, str] = {}
    try:
        for raw in manifest_p.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            parts = raw.split()
            if len(parts) != 2:
                return False, "manifest_format_invalid", None
            digest, name = parts
            name = name.lstrip("*")
            if name in entries or "/" in name or "\\" in name:
                return False, "manifest_entry_invalid", None
            entries[name] = digest.lower()
    except Exception:
        return False, "manifest_unreadable", None

    if set(entries) != actual:
        return False, "manifest_file_set_mismatch", None
    for name, expected in entries.items():
        p = SCORES / name
        actual_hash = hashlib.sha256(p.read_bytes()).hexdigest()
        if actual_hash != expected:
            return False, f"manifest_hash_mismatch:{name}", None

    source_ts = parse_ts(session.get("source_generated_at"))
    latest_ts = parse_ts(latest.get("generated_at"))
    if source_ts is None or latest_ts is None or source_ts != latest_ts:
        return False, "source_generated_at_mismatch", None

    freshness = session.get("freshness")
    if not isinstance(freshness, dict):
        return False, "freshness_metadata_missing", None
    max_age = safe_float(freshness.get("max_age_seconds"), -1)
    max_future = safe_float(freshness.get("max_future_skew_seconds"), -1)
    if max_age <= 0 or max_future < 0:
        return False, "freshness_limits_invalid", None
    age_now = (datetime.now(timezone.utc) - latest_ts).total_seconds()
    if age_now > max_age:
        return False, "evidence_stale", None
    if age_now < -max_future:
        return False, "evidence_from_future", None

    latest_ids = []
    devices = latest.get("devices")
    if not isinstance(devices, list):
        return False, "latest_devices_invalid", None
    for d in devices:
        if not isinstance(d, dict) or not isinstance(d.get("id"), str) or not d["id"].strip():
            return False, "device_identity_invalid", None
        latest_ids.append(d["id"].strip())
    if len(latest_ids) != len(set(latest_ids)):
        return False, "duplicate_device_ids", None
    if session.get("device_count") != len(latest_ids) or session.get("device_ids") != latest_ids:
        return False, "device_inventory_provenance_mismatch", None

    source_ids = session.get("source_ids")
    if not isinstance(source_ids, dict):
        return False, "source_ids_invalid", None
    for key in ("run_id", "session_id", "probe_session_id", "evidence_id"):
        lv = latest.get(key)
        sv = source_ids.get(key)
        if lv is None and sv is None:
            continue
        if lv is None or sv is None or str(lv) != str(sv):
            return False, f"source_id_mismatch:{key}", None

    return True, "ok", session


def main() -> int:
    latest = load_json(SCORES / "latest.json")
    if not latest:
        print("AEGIS_AUTOCHECK status=no_data")
        return 3

    verified, reason, session = verify_export(latest)
    if not verified:
        print(f"AEGIS_AUTOCHECK status=invalid_export reason={reason}")
        return 5

    deepdiag = load_json(SCORES / "deepdiag.json")
    devices = latest.get("devices", [])

    findings = []
    for d in devices:
        score = safe_float(d.get("score"))
        reachable = bool(d.get("reachable", False))
        anomaly = safe_float(d.get("anomaly_score"))
        p = priority(score, reachable, anomaly)
        if p != "P3":
            findings.append({
                "priority": p,
                "device": d.get("id") or d.get("name") or "unknown",
                "score": score,
                "reachable": reachable,
                "anomaly_score": anomaly,
                "status": d.get("status", "unknown"),
            })

    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    findings.sort(key=lambda x: (order[x["priority"]], x["score"]))

    network_score = safe_float(latest.get("network_score"))
    deep_score = safe_float((deepdiag or {}).get("deepdiag_score", latest.get("deepdiag_score")))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_verified": True,
        "evidence_source_generated_at": session.get("source_generated_at") if session else None,
        "evidence_correlation": session.get("correlation") if session else None,
        "network_score": network_score,
        "deepdiag_score": deep_score,
        "device_count": len(devices),
        "priority_findings": findings,
        "recommended_next_step": (
            "stabilize_P0" if any(x["priority"] == "P0" for x in findings)
            else "stabilize_P1" if any(x["priority"] == "P1" for x in findings)
            else "continue_observation"
        ),
    }

    DASH.mkdir(parents=True, exist_ok=True)
    (DASH / "autocheck.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    fix_path = FIXES / "fix-package-002.json"
    if findings:
        FIXES.mkdir(parents=True, exist_ok=True)
        evidence = ["scores/latest.json", "scores/export-session.json", "scores/manifest.sha256"]
        if (SCORES / "deepdiag.json").exists():
            evidence.append("scores/deepdiag.json")
        fix = {
            "package": "fix-package-002",
            "generated_at": report["generated_at"],
            "evidence_verified": True,
            "evidence_source_generated_at": report["evidence_source_generated_at"],
            "evidence": evidence,
            "findings": findings,
            "safety": {
                "automatic_remote_writes": False,
                "destructive_actions": False,
                "requires_verification": True,
                "rollback_required": True,
            },
        }
        fix_path.write_text(json.dumps(fix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    elif fix_path.exists():
        fix_path.unlink()

    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
