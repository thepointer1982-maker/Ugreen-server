#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "aegis_real_cycle.py"
spec = importlib.util.spec_from_file_location("aegis_real_cycle", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def main() -> None:
    report = {
        "inventory": {
            "sqlite_databases": [
                {"path": "/old-trace.db", "mtime": 10, "trace_metrics": {"count": 3}},
                {"path": "/new-trace.db", "mtime": 20, "trace_metrics": {"count": 4}},
                {"path": "/telemetry.db", "mtime": 15, "telemetry_metrics": {"count": 5}},
                {"path": "/other.db", "mtime": 99},
            ]
        }
    }
    selected = mod.select_database_sources(report)
    assert selected["trace_db"] == "/new-trace.db"
    assert selected["telemetry_db"] == "/telemetry.db"

    empty = mod.select_database_sources({})
    assert empty == {"trace_db": None, "telemetry_db": None}
    print("AEGIS REAL CYCLE TESTS PASS")


if __name__ == "__main__":
    main()
