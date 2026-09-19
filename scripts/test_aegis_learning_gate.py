#!/usr/bin/env python3
from __future__ import annotations
import importlib.util
from pathlib import Path

SCRIPT=Path(__file__).resolve().parent/"aegis_learning_gate.py"
spec=importlib.util.spec_from_file_location("gate",SCRIPT)
mod=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(mod)

def main():
    r=mod.evaluate({"score":0.70,"safety":0.9},{"score":0.74,"safety":0.9},
                   min_improvement=0.02,regression_keys=["safety"])
    assert r.accepted and r.status=="accepted"

    r=mod.evaluate({"score":0.70},{"score":0.71},min_improvement=0.02)
    assert not r.accepted and r.reason=="insufficient-improvement"

    r=mod.evaluate({"score":0.70,"safety":0.9},{"score":0.80,"safety":0.7},
                   min_improvement=0.02,regression_keys=["safety"])
    assert not r.accepted and r.reason=="regression-detected"

    r=mod.evaluate({},{"score":0.8})
    assert not r.accepted and r.status=="blocked"

    print("AEGIS LEARNING GATE TESTS PASS")

if __name__=="__main__": main()
