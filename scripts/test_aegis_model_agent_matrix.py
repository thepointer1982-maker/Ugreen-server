#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, sqlite3, tempfile
from pathlib import Path

SCRIPT=Path(__file__).resolve().parent/"aegis_model_agent_matrix.py"
spec=importlib.util.spec_from_file_location("matrix",SCRIPT)
mod=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(mod)

def main():
    with tempfile.TemporaryDirectory() as raw:
        root=Path(raw); tdb=root/"traces.db"; mdb=root/"telemetry.db"
        c=sqlite3.connect(tdb)
        c.execute("CREATE TABLE traces (agent TEXT, model TEXT, outcome TEXT, feedback REAL, total_latency_seconds REAL, total_tokens INTEGER)")
        c.executemany("INSERT INTO traces VALUES (?,?,?,?,?,?)",[
            ("coder","m1","success",0.9,1.0,100),
            ("coder","m1","success",0.8,1.2,100),
            ("coder","m1","failed",0.4,1.5,100),
            ("coder","m2","success",0.95,2.5,100),
            ("coder","m2","success",0.95,2.4,100),
            ("coder","m2","success",0.95,2.6,100),
        ]); c.commit(); c.close()
        c=sqlite3.connect(mdb)
        c.execute("CREATE TABLE telemetry (agent TEXT, model_id TEXT, latency_seconds REAL, throughput_tok_per_sec REAL, total_tokens INTEGER, cost_usd REAL, energy_joules REAL, is_warmup INTEGER)")
        c.executemany("INSERT INTO telemetry VALUES (?,?,?,?,?,?,?,?)",[
            ("coder","m1",1.1,25,100,0,2,0),
            ("coder","m2",2.5,10,100,0,3,0),
        ]); c.commit(); c.close()
        data=mod.build_matrix(mod.trace_rows(tdb),mod.telemetry_rows(mdb),3)
        assert len(data["rows"])==2
        assert data["best_by_agent"]["coder"]["model"] in {"m1","m2"}
        assert all("confidence" in r for r in data["rows"])

        legacy=root/"legacy.db"
        c=sqlite3.connect(legacy)
        c.execute("CREATE TABLE telemetry (agent TEXT, model_id TEXT, latency_seconds REAL, throughput_tok_per_sec REAL, total_tokens INTEGER, cost_usd REAL, energy_joules REAL)")
        c.execute("INSERT INTO telemetry VALUES (?,?,?,?,?,?,?)",("coder","legacy",1.0,5.0,10,0.0,1.0))
        c.commit(); c.close()
        legacy_rows=mod.telemetry_rows(legacy)
        assert len(legacy_rows)==1
        assert legacy_rows[0]["model"]=="legacy"

        telemetry_only = [
            {"agent":"coder","model":"fast","latency":0.2,"throughput":50.0,"tokens":10,"cost":0.0,"energy":0.1}
            for _ in range(50)
        ]
        guarded = mod.build_matrix([], telemetry_only, 3)
        fast = next(r for r in guarded["rows"] if r["model"]=="fast")
        assert fast["telemetry_count"] == 50
        assert fast["trace_count"] == 0
        assert fast["eligible_for_routing"] is False
        assert "coder" not in guarded["best_by_agent"]
    print("AEGIS MODEL AGENT MATRIX TESTS PASS")

if __name__=="__main__": main()
