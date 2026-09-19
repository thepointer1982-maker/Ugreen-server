#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aegis_last_known_good import promote
from aegis_provenance import attach_provenance, verify_provenance


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
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def evaluate(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    score_key: str = "score",
    min_improvement: float = 0.02,
    score_scale: float = 1.0,
    regression_keys: list[str] | None = None,
    max_regression: float = 0.0,
) -> GateResult:
    regression_keys = regression_keys or []
    baseline_score = number(baseline, score_key)
    candidate_score = number(candidate, score_key)
    if baseline_score is None or candidate_score is None:
        return GateResult(
            False,
            "blocked",
            "missing-or-invalid-eval-score",
            None,
            [],
        )
    if score_scale <= 0:
        return GateResult(False, "blocked", "invalid-score-scale", None, [])

    improvement = (candidate_score - baseline_score) / score_scale
    regressions: list[str] = []
    for key in regression_keys:
        baseline_value = number(baseline, key)
        candidate_value = number(candidate, key)
        if baseline_value is None or candidate_value is None:
            regressions.append(f"{key}:missing")
            continue
        if candidate_value < baseline_value - max_regression:
            regressions.append(
                f"{key}:{baseline_value:.6f}->{candidate_value:.6f}"
            )

    if regressions:
        return GateResult(
            False,
            "rejected",
            "regression-detected",
            improvement,
            regressions,
        )
    if improvement < min_improvement:
        return GateResult(
            False,
            "rejected",
            "insufficient-improvement",
            improvement,
            [],
        )
    return GateResult(
        True,
        "accepted",
        "evidence-gate-passed",
        improvement,
        [],
    )


def _write_output(path: Path | None, payload: dict[str, Any]) -> str:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    return text


def _blocked_provenance_payload(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    baseline_ok: bool,
    baseline_reason: str,
    candidate_ok: bool,
    candidate_reason: str,
) -> dict[str, Any]:
    return attach_provenance(
        {
            "schema": "aegis-learning-gate/v2",
            "accepted": False,
            "status": "blocked",
            "reason": "provenance-verification-failed",
            "improvement": None,
            "regressions": [],
            "evidence": {
                "baseline": {
                    "verified": baseline_ok,
                    "reason": baseline_reason,
                    "sha256": (
                        baseline.get("_provenance", {}).get("sha256")
                        if isinstance(baseline.get("_provenance"), dict)
                        else None
                    ),
                },
                "candidate": {
                    "verified": candidate_ok,
                    "reason": candidate_reason,
                    "sha256": (
                        candidate.get("_provenance", {}).get("sha256")
                        if isinstance(candidate.get("_provenance"), dict)
                        else None
                    ),
                },
            },
            "policy": {"fail_closed": True},
        },
        kind="learning-gate",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed AEGIS learning acceptance gate."
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--score-key", default="score")
    parser.add_argument("--min-improvement", type=float, default=0.02)
    parser.add_argument(
        "--score-scale",
        type=float,
        default=1.0,
        help="1 for 0..1 scores, 100 for 0..100 scores.",
    )
    parser.add_argument("--regression-key", action="append", default=[])
    parser.add_argument("--max-regression", type=float, default=0.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--promote-kind",
        help="Promote accepted candidate to last-known-good under this kind.",
    )
    args = parser.parse_args()

    baseline = load(args.baseline)
    candidate = load(args.candidate)
    baseline_ok, baseline_reason = verify_provenance(baseline)
    candidate_ok, candidate_reason = verify_provenance(candidate)

    if not baseline_ok or not candidate_ok:
        payload = _blocked_provenance_payload(
            baseline,
            candidate,
            baseline_ok=baseline_ok,
            baseline_reason=baseline_reason,
            candidate_ok=candidate_ok,
            candidate_reason=candidate_reason,
        )
        print(_write_output(args.output, payload), end="")
        return 3

    result = evaluate(
        baseline,
        candidate,
        score_key=args.score_key,
        min_improvement=args.min_improvement,
        score_scale=args.score_scale,
        regression_keys=args.regression_key,
        max_regression=args.max_regression,
    )

    candidate_sha = candidate["_provenance"]["sha256"]
    candidate_kind = str(
        candidate["_provenance"].get("kind") or "candidate"
    )

    payload = attach_provenance(
        {
            "schema": "aegis-learning-gate/v2",
            "accepted": result.accepted,
            "status": result.status,
            "reason": result.reason,
            "improvement": result.improvement,
            "regressions": result.regressions,
            "evidence": {
                "baseline_sha256": baseline["_provenance"]["sha256"],
                "candidate_sha256": candidate_sha,
            },
            "policy": {
                "fail_closed": True,
                "min_improvement": args.min_improvement,
                "score_scale": args.score_scale,
                "score_key": args.score_key,
                "regression_keys": args.regression_key,
                "max_regression": args.max_regression,
            },
        },
        kind="learning-gate",
        parent_sha256=candidate_sha,
        parent_kind=candidate_kind,
    )

    if result.accepted and args.promote_kind:
        gate_sha = payload["_provenance"]["sha256"]
        promotion = promote(
            args.candidate,
            kind=args.promote_kind,
            metadata={
                "baseline_sha256": baseline["_provenance"]["sha256"],
                "candidate_sha256": candidate_sha,
                "learning_gate_sha256": gate_sha,
            },
        )
        if promotion.get("status") != "promoted":
            payload["accepted"] = False
            payload["status"] = "blocked"
            payload["reason"] = "last-known-good-promotion-failed"
            payload["promotion"] = promotion
        else:
            payload["promotion"] = promotion

        payload = attach_provenance(
            payload,
            kind="learning-gate",
            parent_sha256=candidate_sha,
            parent_kind=candidate_kind,
        )

    print(_write_output(args.output, payload), end="")
    return 0 if payload.get("accepted") is True else 3


if __name__ == "__main__":
    raise SystemExit(main())
