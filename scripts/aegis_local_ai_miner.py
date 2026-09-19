#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_DIR = Path(os.environ.get("AEGIS_AI_MINER_STATE_DIR", Path.home() / ".local/state/aegis-ai-miner"))
REPORT = STATE_DIR / "latest.json"
HISTORY = STATE_DIR / "history.jsonl"

DEFAULT_ROOTS = [
    Path.home() / ".openjarvis",
    Path("/opt/aegis"),
    Path("/var/log/aegis"),
    Path("/volume1/docker/aegis-field"),
    Path.home() / ".local/state/aegis-guardian",
    Path.home() / ".local/state/aegis-scheduler",
]
TEXT_SUFFIXES = {".json", ".jsonl", ".log", ".txt", ".toml", ".md", ".yaml", ".yml"}
DB_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
MAX_SCAN_FILES = 5000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_stat(path: Path) -> dict[str, Any]:
    try:
        st = path.stat()
        return {"size_bytes": st.st_size, "mtime": st.st_mtime}
    except OSError:
        return {"size_bytes": None, "mtime": None}


def sqlite_summary(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), **safe_stat(path), "tables": {}, "error": None}
    try:
        uri = f"file:{path.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        for table in tables[:50]:
            if not table.replace("_", "").isalnum():
                continue
            try:
                count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            except sqlite3.Error:
                count = None
            result["tables"][table] = {"rows": count}

        if "traces" in tables:
            row = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END), "
                "AVG(feedback), AVG(total_latency_seconds), SUM(total_tokens) FROM traces"
            ).fetchone()
            result["trace_metrics"] = {
                "count": row[0] or 0,
                "successes": row[1] or 0,
                "avg_feedback": row[2],
                "avg_latency_seconds": row[3],
                "total_tokens": row[4] or 0,
            }
            models = conn.execute(
                "SELECT model, COUNT(*) n, AVG(feedback), AVG(total_latency_seconds) "
                "FROM traces GROUP BY model ORDER BY n DESC LIMIT 20"
            ).fetchall()
            result["trace_models"] = [
                {"model": r[0], "count": r[1], "avg_feedback": r[2], "avg_latency_seconds": r[3]}
                for r in models
            ]

        if "telemetry" in tables:
            row = conn.execute(
                "SELECT COUNT(*), SUM(total_tokens), AVG(latency_seconds), AVG(throughput_tok_per_sec), "
                "SUM(cost_usd), AVG(energy_joules), AVG(power_watts) FROM telemetry"
            ).fetchone()
            result["telemetry_metrics"] = {
                "count": row[0] or 0,
                "total_tokens": row[1] or 0,
                "avg_latency_seconds": row[2],
                "avg_throughput_tok_s": row[3],
                "cost_usd": row[4] or 0,
                "avg_energy_joules": row[5],
                "avg_power_watts": row[6],
            }
            models = conn.execute(
                "SELECT model_id, COUNT(*) n, AVG(latency_seconds), AVG(throughput_tok_per_sec), SUM(total_tokens) "
                "FROM telemetry GROUP BY model_id ORDER BY n DESC LIMIT 20"
            ).fetchall()
            result["telemetry_models"] = [
                {"model": r[0], "count": r[1], "avg_latency_seconds": r[2],
                 "avg_throughput_tok_s": r[3], "total_tokens": r[4] or 0}
                for r in models
            ]

        if "entities" in tables and "relations" in tables:
            result["knowledge_graph"] = {
                "entities": conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
                "relations": conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
                "entity_types": [
                    {"type": r[0], "count": r[1]}
                    for r in conn.execute(
                        "SELECT entity_type, COUNT(*) FROM entities GROUP BY entity_type ORDER BY COUNT(*) DESC LIMIT 30"
                    )
                ],
                "relation_types": [
                    {"type": r[0], "count": r[1]}
                    for r in conn.execute(
                        "SELECT relation_type, COUNT(*) FROM relations GROUP BY relation_type ORDER BY COUNT(*) DESC LIMIT 30"
                    )
                ],
            }
        conn.close()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def ollama_summary() -> dict[str, Any]:
    result = {"reachable": False, "models": [], "error": None}
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags", headers={"User-Agent": "AEGIS-local-miner/1"})
        with urllib.request.urlopen(req, timeout=2) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        result["reachable"] = True
        for model in data.get("models", []):
            if not isinstance(model, dict):
                continue
            details = model.get("details") if isinstance(model.get("details"), dict) else {}
            result["models"].append({
                "name": model.get("name"),
                "size": model.get("size"),
                "modified_at": model.get("modified_at"),
                "family": details.get("family"),
                "parameter_size": details.get("parameter_size"),
                "quantization_level": details.get("quantization_level"),
            })
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def scan_roots(roots: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    files: list[dict[str, Any]] = []
    dbs: list[dict[str, Any]] = []
    suffix_counts: Counter[str] = Counter()
    seen = 0
    for root in roots:
        try:
            exists = root.exists()
        except OSError:
            exists = False
        if not exists:
            continue
        for path in root.rglob("*"):
            if seen >= MAX_SCAN_FILES:
                break
            try:
                if not path.is_file() or path.is_symlink():
                    continue
            except OSError:
                continue
            seen += 1
            suffix = path.suffix.lower()
            suffix_counts[suffix or "(none)"] += 1
            if suffix in DB_SUFFIXES:
                dbs.append(sqlite_summary(path))
            elif suffix in TEXT_SUFFIXES:
                meta = {"path": str(path), "suffix": suffix, **safe_stat(path)}
                files.append(meta)
        if seen >= MAX_SCAN_FILES:
            break
    return files, dbs, dict(suffix_counts)


def guardian_summary() -> dict[str, Any]:
    root = Path.home() / ".local/state/aegis-guardian"
    status = {}
    cards: list[dict[str, Any]] = []
    try:
        p = root / "status.json"
        if p.is_file():
            value = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                status = value
    except Exception:
        pass
    try:
        p = root / "learning-cards.jsonl"
        if p.is_file():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        cards.append(value)
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    patterns = Counter((c.get("reason"), c.get("action"), c.get("outcome")) for c in cards)
    return {
        "status": status,
        "learning_card_count_sampled": len(cards),
        "top_patterns": [
            {"reason": k[0], "action": k[1], "outcome": k[2], "count": v}
            for k, v in patterns.most_common(20)
        ],
    }


def derive_findings(dbs: list[dict[str, Any]], ollama: dict[str, Any], guardian: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    has_traces = any("trace_metrics" in db for db in dbs)
    has_telemetry = any("telemetry_metrics" in db for db in dbs)
    has_graph = any("knowledge_graph" in db for db in dbs)
    if not has_traces:
        findings.append({"severity": "medium", "code": "TRACE_STORE_NOT_FOUND", "lesson": "No local trace SQLite store was discovered."})
    if not has_telemetry:
        findings.append({"severity": "medium", "code": "TELEMETRY_STORE_NOT_FOUND", "lesson": "No local inference telemetry SQLite store was discovered."})
    if not has_graph:
        findings.append({"severity": "low", "code": "KNOWLEDGE_GRAPH_NOT_FOUND", "lesson": "No local knowledge graph database was discovered."})
    if not ollama.get("reachable"):
        findings.append({"severity": "medium", "code": "OLLAMA_NOT_REACHABLE", "lesson": "Local Ollama API was not reachable on loopback."})
    if guardian.get("status", {}).get("mode") in {"degraded", "emergency", "blocked"}:
        findings.append({"severity": "high", "code": "GUARDIAN_NOT_HEALTHY", "lesson": "Guardian state requires attention before learning changes are accepted."})

    for db in dbs:
        metrics = db.get("trace_metrics")
        if metrics and metrics.get("count", 0) >= 10:
            successes = metrics.get("successes", 0)
            count = metrics.get("count", 0)
            rate = successes / count if count else 0
            if rate < 0.7:
                findings.append({
                    "severity": "medium",
                    "code": "TRACE_SUCCESS_RATE_LOW",
                    "lesson": f"Trace success rate is {rate:.1%} across {count} traces; mine failure classes before changing models.",
                    "source": db.get("path"),
                })
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only local AI/protocol inventory and learning evidence miner.")
    parser.add_argument("--root", action="append", default=[], help="Additional local root to scan read-only.")
    args = parser.parse_args()

    roots = DEFAULT_ROOTS + [Path(p).expanduser() for p in args.root]
    files, dbs, suffix_counts = scan_roots(roots)
    ollama = ollama_summary()
    guardian = guardian_summary()
    findings = derive_findings(dbs, ollama, guardian)

    report = {
        "schema": "aegis-local-ai-miner/v1",
        "generated_at": now_iso(),
        "mode": "read-only-local-evidence",
        "roots": [{"path": str(p), "exists": p.exists()} for p in roots],
        "inventory": {
            "text_protocol_files": files,
            "sqlite_databases": dbs,
            "suffix_counts": suffix_counts,
        },
        "ollama": ollama,
        "guardian": guardian,
        "findings": findings,
        "guardrails": {
            "raw_prompt_export": False,
            "raw_message_export": False,
            "database_write": False,
            "model_download": False,
            "network_target": "127.0.0.1:11434 only",
        },
    }

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REPORT.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(REPORT)
    with HISTORY.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "generated_at": report["generated_at"],
            "db_count": len(dbs),
            "ollama_reachable": ollama["reachable"],
            "ollama_models": len(ollama["models"]),
            "findings": [f["code"] for f in findings],
        }, sort_keys=True) + "\n")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
