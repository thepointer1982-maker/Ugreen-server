#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
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
            "CREATE TABLE traces (trace_id TEXT, outcome TEXT, feedback REAL, total_latency_seconds REAL, "
            "total_tokens INTEGER, model TEXT)"
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

        files, dbs, counts = mod.scan_roots([root])
        assert len(dbs) == 1
        assert counts[".db"] == 1
        assert files == []

    findings = mod.derive_findings([], {"reachable": False}, {"status": {"mode": "healthy"}})
    codes = {f["code"] for f in findings}
    assert "TRACE_STORE_NOT_FOUND" in codes
    assert "TELEMETRY_STORE_NOT_FOUND" in codes
    assert "OLLAMA_NOT_REACHABLE" in codes
    print("AEGIS LOCAL AI MINER TESTS PASS")


if __name__ == "__main__":
    main()
