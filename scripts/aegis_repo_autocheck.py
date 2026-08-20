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


def priority(score: float, reachable: bool, anomaly: float) -> str:
    if not reachable or score < 40 or anomaly >= 90:
        return "P0"
    if score < 60 or anomaly >= 70:
        return "P1"
    if score < 80 or anomaly >= 50:
        return "P2"
    return "P3"


def verify_export() -> tuple[bool, str]:
    session_p = SCORES / "export-session.json"
    manifest_p = SCORES / "manifest.sha256"
    if not session_p.exists() or not manifest_p.exists():
        return False, "export_session_or_manifest_missing"
    try:
        session = load_json(session_p)
    except Exception:
        return False, "export_session_invalid_json"
    if not isinstance(session, dict) or session.get("schema_version") != 1:
        return False, "export_session_invalid"
    listed = session.get("files")
    if not isinstance(listed, list) or not all(isinstance(x, str) for x in listed):
        return False, "export_session_files_invalid"
    expected_session_files = set(listed) | {"export-session.json"}
    actual = {p.name for p in SCORES.iterdir() if p.is_file() and p.name in GENERATED_SCORE_FILES}
    if actual != expected_session_files:
        return False, "export_session_file_set_mismatch"

    entries: dict[str, str] = {}
    try:
        for raw in manifest_p.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            parts = raw.split()
            if len(parts) != 2:
                return False, "manifest_format_invalid"
            digest, name = parts
            name = name.lstrip("*")
            if name in entries or "/" in name or "\\" in name:
                return False, "manifest_entry_invalid"
            entries[name] = digest.lower()
    except Exception:
        return False, "manifest_unreadable"

    if set(entries) != actual:
        return False, "manifest_file_set_mismatch"
    for name, expected in entries.items():
        p = SCORES / name
        actual_hash = hashlib.sha256(p.read_bytes()).hexdigest()
        if actual_hash != expected:
            return False, f"manifest_hash_mismatch:{name}"
    return True, "ok"


def main() -> int:
    latest = load_json(SCORES / "latest.json")
    if not latest:
        print("AEGIS_AUTOCHECK status=no_data")
        return 3

    verified, reason = verify_export()
    if not verified:
        print(f"AEGIS_AUTOCHECK status=invalid_export reason={reason}")
        return 5

    deepdiag = load_json(SCORES / "deepdiag.json")
    devices = latest.get("devices", [])
    if not isinstance(devices, list):
        print("AEGIS_AUTOCHECK status=invalid_devices")
        return 4

    findings = []
    for d in devices:
        if not isinstance(d, dict):
            continue
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
