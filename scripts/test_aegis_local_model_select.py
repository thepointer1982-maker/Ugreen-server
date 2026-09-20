#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, os, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
SCRIPT=ROOT/"scripts"/"aegis_local_model_select.py"
spec=importlib.util.spec_from_file_location("sel",SCRIPT)
mod=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(mod)

def main():
    reg=json.loads((ROOT/"config/models/aegis-local-models.json").read_text(encoding="utf-8"))
    assert reg["policy"]["auto_download_optional_models"] is False
    assert mod.infer_role("debug root cause analyse")=="reasoning"
    assert mod.infer_role("implement python test")=="coding"
    installed={"qwen2.5-coder:7b","deepseek-r1:8b"}
    r=mod.choose_model(reg,installed,role="coding")
    assert r["model"]=="qwen2.5-coder:7b"
    r=mod.choose_model(reg,installed,role="reasoning")
    assert r["model"]=="deepseek-r1:8b"
    r=mod.choose_model(reg,{"qwen2.5-coder:7b"},role="reasoning")
    assert r["model"]=="qwen2.5-coder:7b"
    r=mod.choose_model(reg,set(),role="coding")
    assert r["status"]=="blocked"
    legacy=next(x for x in reg["models"] if x["id"]=="deepseek-coder:6.7b")
    assert legacy["auto_select"] is False
    print("AEGIS LOCAL MODEL SELECT TESTS PASS")
if __name__=="__main__": main()
