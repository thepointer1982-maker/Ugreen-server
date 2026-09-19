#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aegis_provenance import attach_provenance

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

def open_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=3)

def has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None

def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}

def trace_rows(path: Path, *, since: float | None = None) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    conn = open_ro(path)
    try:
        if not has_table(conn, "traces"):
            return []
        cols = columns(conn, "traces")
        required = {
            "agent", "model", "outcome", "feedback",
            "total_latency_seconds", "total_tokens",
        }
        if not required.issubset(cols):
            return []
        has_time = "started_at" in cols
        sql = (
            "SELECT agent, model, outcome, feedback, total_latency_seconds, total_tokens"
            + (", started_at" if has_time else "")
            + " FROM traces"
        )
        params: tuple[Any, ...] = ()
        if since is not None and has_time:
            sql += " WHERE started_at >= ?"
            params = (float(since),)
        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "agent": r[0] or "unknown",
                "model": r[1] or "unknown",
                "outcome": r[2],
                "feedback": r[3],
                "latency": float(r[4] or 0.0),
                "tokens": int(r[5] or 0),
                "timestamp": float(r[6]) if len(r) > 6 and r[6] is not None else None,
            }
            for r in rows
        ]
    finally:
        conn.close()

def telemetry_rows(path: Path, *, since: float | None = None) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    conn = open_ro(path)
    try:
        if not has_table(conn, "telemetry"):
            return []
        cols = columns(conn, "telemetry")
        required = {
            "agent", "model_id", "latency_seconds", "throughput_tok_per_sec",
            "total_tokens", "cost_usd", "energy_joules",
        }
        missing = required - cols
        if missing:
            return []
        has_time = "timestamp" in cols
        clauses: list[str] = []
        params: list[Any] = []
        if "is_warmup" in cols:
            clauses.append("is_warmup=0")
        if since is not None and has_time:
            clauses.append("timestamp >= ?")
            params.append(float(since))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            "SELECT agent, model_id, latency_seconds, throughput_tok_per_sec, total_tokens, cost_usd, energy_joules"
            + (", timestamp" if has_time else "")
            + " FROM telemetry" + where,
            params,
        ).fetchall()
        return [
            {
                "agent": r[0] or "unknown",
                "model": r[1] or "unknown",
                "latency": float(r[2] or 0.0),
                "throughput": float(r[3] or 0.0),
                "tokens": int(r[4] or 0),
                "cost": float(r[5] or 0.0),
                "energy": float(r[6] or 0.0),
                "timestamp": float(r[7]) if len(r) > 7 and r[7] is not None else None,
            }
            for r in rows
        ]
    finally:
        conn.close()

def bounded(v: float) -> float:
    return max(0.0, min(1.0, v))

def build_matrix(
    traces: list[dict[str,Any]],
    telemetry: list[dict[str,Any]],
    min_samples: int = 3,
    *,
    max_age_seconds: int | None = None,
    now_ts: float | None = None,
) -> dict[str,Any]:
    now_ts = time.time() if now_ts is None else float(now_ts)
    cutoff = now_ts - max_age_seconds if max_age_seconds is not None else None
    if cutoff is not None:
        traces = [r for r in traces if r.get("timestamp") is None or float(r["timestamp"]) >= cutoff]
        telemetry = [r for r in telemetry if r.get("timestamp") is None or float(r["timestamp"]) >= cutoff]

    groups: dict[tuple[str,str], dict[str,Any]] = defaultdict(lambda: {
        "trace_count":0,"dated_trace_count":0,"successes":0,"feedback_sum":0.0,"feedback_n":0,
        "trace_latency_sum":0.0,"trace_tokens":0,"latest_trace_ts":None,
        "telemetry_count":0,"dated_telemetry_count":0,"telemetry_latency_sum":0.0,"throughput_sum":0.0,
        "telemetry_tokens":0,"cost_sum":0.0,"energy_sum":0.0,"latest_telemetry_ts":None
    })
    for r in traces:
        g = groups[(r["agent"],r["model"])]
        g["trace_count"] += 1
        if r.get("timestamp") is not None:
            g["dated_trace_count"] += 1
            ts = float(r["timestamp"])
            g["latest_trace_ts"] = ts if g["latest_trace_ts"] is None else max(g["latest_trace_ts"], ts)
        g["successes"] += 1 if r["outcome"] == "success" else 0
        if r["feedback"] is not None:
            g["feedback_sum"] += float(r["feedback"])
            g["feedback_n"] += 1
        g["trace_latency_sum"] += r["latency"]
        g["trace_tokens"] += r["tokens"]

    for r in telemetry:
        g = groups[(r["agent"],r["model"])]
        g["telemetry_count"] += 1
        if r.get("timestamp") is not None:
            g["dated_telemetry_count"] += 1
            ts = float(r["timestamp"])
            g["latest_telemetry_ts"] = ts if g["latest_telemetry_ts"] is None else max(g["latest_telemetry_ts"], ts)
        g["telemetry_latency_sum"] += r["latency"]
        g["throughput_sum"] += r["throughput"]
        g["telemetry_tokens"] += r["tokens"]
        g["cost_sum"] += r["cost"]
        g["energy_sum"] += r["energy"]

    rows = []
    for (agent,model), g in sorted(groups.items()):
        trace_n = g["trace_count"]
        dated_trace_n = g["dated_trace_count"]
        tel_n = g["telemetry_count"]
        dated_tel_n = g["dated_telemetry_count"]
        success_rate = g["successes"]/trace_n if trace_n else None
        avg_feedback = g["feedback_sum"]/g["feedback_n"] if g["feedback_n"] else None
        avg_latency = (
            (g["trace_latency_sum"] + g["telemetry_latency_sum"]) /
            max(1, trace_n + tel_n)
        )
        avg_throughput = g["throughput_sum"]/tel_n if tel_n else None

        quality = (
            0.7*(success_rate if success_rate is not None else 0.5) +
            0.3*(avg_feedback if avg_feedback is not None else 0.5)
        )
        latency_score = 1.0 / (1.0 + max(0.0, avg_latency))
        throughput_score = 0.5 if avg_throughput is None else bounded(avg_throughput / 30.0)
        efficiency = 0.5*latency_score + 0.5*throughput_score

        quality_confidence = bounded(
            math.log2(trace_n + 1) / math.log2(max(min_samples, 8) + 1)
        )
        efficiency_confidence = bounded(
            math.log2(tel_n + 1) / math.log2(max(min_samples, 8) + 1)
        ) if tel_n else 0.0
        confidence = 0.75 * quality_confidence + 0.25 * efficiency_confidence
        composite = bounded(
            (0.70*quality + 0.30*efficiency) * (0.5 + 0.5*confidence)
        )

        rows.append({
            "agent":agent,
            "model":model,
            "trace_count":trace_n,
            "dated_trace_count":dated_trace_n,
            "telemetry_count":tel_n,
            "dated_telemetry_count":dated_tel_n,
            "latest_trace_timestamp":g["latest_trace_ts"],
            "latest_telemetry_timestamp":g["latest_telemetry_ts"],
            "success_rate":success_rate,
            "avg_feedback":avg_feedback,
            "avg_latency_seconds":avg_latency,
            "avg_throughput_tok_s":avg_throughput,
            "total_tokens":g["trace_tokens"] + g["telemetry_tokens"],
            "cost_usd":g["cost_sum"],
            "energy_joules":g["energy_sum"],
            "quality_score":round(quality,4),
            "efficiency_score":round(efficiency,4),
            "quality_confidence":round(quality_confidence,4),
            "efficiency_confidence":round(efficiency_confidence,4),
            "confidence":round(confidence,4),
            "composite_score":round(composite,4),
            "eligible_for_routing": (
                trace_n >= min_samples
                and (max_age_seconds is None or dated_trace_n >= min_samples)
            ),
        })

    eligible = [r for r in rows if r["eligible_for_routing"]]
    best_by_agent = {}
    for r in eligible:
        cur = best_by_agent.get(r["agent"])
        if cur is None or r["composite_score"] > cur["composite_score"]:
            best_by_agent[r["agent"]] = r
    return {
        "schema":"aegis-model-agent-matrix/v2",
        "generated_at":now_iso(),
        "min_samples":min_samples,
        "max_age_seconds":max_age_seconds,
        "cutoff_timestamp":cutoff,
        "rows":rows,
        "best_by_agent":best_by_agent,
    }

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--trace-db", type=Path, required=True)
    p.add_argument("--telemetry-db", type=Path, required=True)
    p.add_argument("--min-samples", type=int, default=3)
    p.add_argument("--max-age-hours", type=float, default=168.0)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()

    if args.max_age_hours <= 0:
        raise SystemExit("--max-age-hours must be > 0")
    cutoff = time.time() - args.max_age_hours * 3600.0
    data = build_matrix(
        trace_rows(args.trace_db, since=cutoff),
        telemetry_rows(args.telemetry_db, since=cutoff),
        args.min_samples,
        max_age_seconds=int(args.max_age_hours * 3600.0),
    )
    source_fingerprints = {
        "trace_db": {
            "path": str(args.trace_db),
            "size_bytes": args.trace_db.stat().st_size if args.trace_db.exists() else None,
            "mtime": args.trace_db.stat().st_mtime if args.trace_db.exists() else None,
        },
        "telemetry_db": {
            "path": str(args.telemetry_db),
            "size_bytes": args.telemetry_db.stat().st_size if args.telemetry_db.exists() else None,
            "mtime": args.telemetry_db.stat().st_mtime if args.telemetry_db.exists() else None,
        },
    }
    data["source_fingerprints"] = source_fingerprints
    data = attach_provenance(data, kind="model-agent-matrix")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(args.output)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
