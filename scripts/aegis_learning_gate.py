#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

@dataclass
class GateResult:
    accepted: bool
    status: str
    reason: str
    improvement: float | None
    regressions: list[str]

def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value

def number(obj: dict[str, Any], key: str) -> float | None:
    v = obj.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)

def evaluate(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    score_key: str = "score",
    min_improvement: float = 0.02,
    regression_keys: list[str] | None = None,
    max_regression: float = 0.0,
) -> GateResult:
    regression_keys = regression_keys or []
    b = number(baseline, score_key)
    c = number(candidate, score_key)
    if b is None or c is None:
        return GateResult(False, "blocked", "missing-or-invalid-eval-score", None, [])

    improvement = c - b
    regressions: list[str] = []
    for key in regression_keys:
        bv, cv = number(baseline, key), number(candidate, key)
        if bv is None or cv is None:
            regressions.append(f"{key}:missing")
            continue
        if cv < bv - max_regression:
            regressions.append(f"{key}:{bv:.6f}->{cv:.6f}")

    if regressions:
        return GateResult(False, "rejected", "regression-detected", improvement, regressions)
    if improvement < min_improvement:
        return GateResult(False, "rejected", "insufficient-improvement", improvement, [])
    return GateResult(True, "accepted", "evidence-gate-passed", improvement, [])

def main() -> int:
    p = argparse.ArgumentParser(description="Fail-closed AEGIS learning acceptance gate.")
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--score-key", default="score")
    p.add_argument("--min-improvement", type=float, default=0.02)
    p.add_argument("--regression-key", action="append", default=[])
    p.add_argument("--max-regression", type=float, default=0.0)
    p.add_argument("--output", type=Path)
    args = p.parse_args()

    result = evaluate(
        load(args.baseline),
        load(args.candidate),
        score_key=args.score_key,
        min_improvement=args.min_improvement,
        regression_keys=args.regression_key,
        max_regression=args.max_regression,
    )
    payload = {
        "schema":"aegis-learning-gate/v1",
        "accepted":result.accepted,
        "status":result.status,
        "reason":result.reason,
        "improvement":result.improvement,
        "regressions":result.regressions,
        "policy":{
            "fail_closed":True,
            "min_improvement":args.min_improvement,
            "score_key":args.score_key,
            "regression_keys":args.regression_key,
            "max_regression":args.max_regression,
        },
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        tmp = args.output.with_suffix(args.output.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(args.output)
    print(text, end="")
    return 0 if result.accepted else 3

if __name__ == "__main__":
    raise SystemExit(main())
