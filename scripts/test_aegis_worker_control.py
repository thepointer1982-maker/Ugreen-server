#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
SCRIPT=ROOT/"scripts"/"aegis_worker_control.py"
spec=importlib.util.spec_from_file_location("workers",SCRIPT)
mod=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(mod)

def main():
    cfg=json.loads((ROOT/"config/workers/aegis-workers.json").read_text(encoding="utf-8"))
    assert cfg["policy"]["arbitrary_target_denied"] is True
    mac=mod.worker_by_id(cfg,"mac-mini-m2")
    assert mac is not None
    assert mod.configured(mac) is False
    old=mod.CONFIG
    with tempfile.TemporaryDirectory() as raw:
        p=Path(raw)/"workers.json"
        p.write_text(json.dumps(cfg),encoding="utf-8")
        mod.CONFIG=p
        assert mod.wake("unknown",dry_run=True)["reason"]=="worker-not-allowlisted"
        assert mod.wake("mac-mini-m2",dry_run=True)["reason"]=="worker-not-configured"
        cfg["workers"][0]["enabled"]=True
        cfg["workers"][0]["wol"]["enabled"]=True
        cfg["workers"][0]["wol"]["mac"]="00:11:22:33:44:55"
        p.write_text(json.dumps(cfg),encoding="utf-8")
        r=mod.wake("mac-mini-m2",dry_run=True)
        assert r["status"]=="dry-run"
        assert r["payload_bytes"]==102
        assert r["port"]==9
    mod.CONFIG=old
    print("AEGIS WORKER CONTROL TESTS PASS")
if __name__=="__main__": main()
