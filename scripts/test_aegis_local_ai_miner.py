#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "aegis_local_ai_miner.py"
spec = importlib.util.spec_from_file_location("aegis_local_ai_miner", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        db = root / "traces.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE traces (trace_id TEXT, outcome TEXT, feedback REAL, "
            "total_latency_seconds REAL, total_tokens INTEGER, model TEXT)"
        )
        conn.executemany(
            "INSERT INTO traces VALUES (?,?,?,?,?,?)",
            [
                ("1", "success", 0.9, 1.0, 10, "local-a"),
                ("2", "failed", 0.2, 3.0, 20, "local-a"),
            ],
        )
        conn.commit()
        conn.close()

        summary = mod.sqlite_summary(db)
        assert summary["tables"]["traces"]["rows"] == 2
        assert summary["trace_metrics"]["count"] == 2
        assert summary["trace_metrics"]["successes"] == 1
        assert summary["trace_models"][0]["model"] == "local-a"

        stats: dict[str, int] = {}
        files, dbs, counts = mod.scan_roots([root], scan_stats=stats)
        assert len(dbs) == 1
        assert counts[".db"] == 1
        assert files == []
        assert stats["db_cache_misses"] == 1
        assert stats["db_cache_hits"] == 0
        assert dbs[0]["cache_reused"] is False

        cache = {str(db): dbs[0]}
        stats2: dict[str, int] = {}
        _, dbs2, _ = mod.scan_roots(
            [root],
            db_cache=cache,
            scan_stats=stats2,
        )
        assert stats2["db_cache_hits"] == 1
        assert stats2["db_cache_misses"] == 0
        assert dbs2[0]["cache_reused"] is True
        assert dbs2[0]["trace_metrics"]["count"] == 2

        time.sleep(0.01)
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO traces VALUES (?,?,?,?,?,?)",
            ("3", "success", 1.0, 0.5, 5, "local-b"),
        )
        conn.commit()
        conn.close()

        stats3: dict[str, int] = {}
        _, dbs3, _ = mod.scan_roots(
            [root],
            db_cache=cache,
            scan_stats=stats3,
        )
        assert stats3["db_cache_hits"] == 0
        assert stats3["db_cache_misses"] == 1
        assert dbs3[0]["trace_metrics"]["count"] == 3

        ignored = root / ".git"
        ignored.mkdir()
        (ignored / "ignored.db").write_bytes(b"not-a-db")
        stats4: dict[str, int] = {}
        _, dbs4, _ = mod.scan_roots([root], scan_stats=stats4)
        assert len(dbs4) == 1
        assert stats4["dirs_pruned"] >= 1

    findings = mod.derive_findings(
        [],
        {"reachable": False},
        {"status": {"mode": "healthy"}},
    )
    codes = {f["code"] for f in findings}
    assert "TRACE_STORE_NOT_FOUND" in codes
    assert "TELEMETRY_STORE_NOT_FOUND" in codes
    assert "OLLAMA_NOT_REACHABLE" in codes

    print("AEGIS LOCAL AI MINER TESTS PASS")


if __name__ == "__main__":
    main()
