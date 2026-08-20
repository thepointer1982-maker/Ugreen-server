#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
SCORES = ROOT / "scores"
FIXES = ROOT / "fixes" / "done"
DASH = ROOT / "dashboard"


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


def main() -> int:
    latest = load_json(SCORES / "latest.json")
    deepdiag = load_json(SCORES / "deepdiag.json")
    if not latest:
        print("AEGIS_AUTOCHECK status=no_data")
        return 3

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

    if findings:
        FIXES.mkdir(parents=True, exist_ok=True)
        evidence = ["scores/latest.json"]
        if (SCORES / "deepdiag.json").exists():
            evidence.append("scores/deepdiag.json")
        fix = {
            "package": "fix-package-002",
            "generated_at": report["generated_at"],
            "evidence": evidence,
            "findings": findings,
            "safety": {
                "automatic_remote_writes": False,
                "destructive_actions": False,
                "requires_verification": True,
                "rollback_required": True,
            },
        }
        (FIXES / "fix-package-002.json").write_text(json.dumps(fix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
