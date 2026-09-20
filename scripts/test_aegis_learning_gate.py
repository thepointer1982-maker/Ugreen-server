#!/usr/bin/env python3
from __future__ import annotations
import importlib.util
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aegis_provenance import attach_provenance, verify_provenance

SCRIPT=Path(__file__).resolve().parent/"aegis_learning_gate.py"
spec=importlib.util.spec_from_file_location("gate",SCRIPT)
assert spec and spec.loader
mod=importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

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

    r=mod.evaluate({"score":70.0},{"score":73.0},min_improvement=0.02,score_scale=100.0)
    assert r.accepted and abs(r.improvement-0.03)<1e-9

    r=mod.evaluate({"score":70.0},{"score":71.0},min_improvement=0.02,score_scale=100.0)
    assert not r.accepted and r.reason=="insufficient-improvement"

    r=mod.evaluate({"score":1.0},{"score":2.0},score_scale=0.0)
    assert not r.accepted and r.reason=="invalid-score-scale"

    baseline = attach_provenance({"score":0.70}, kind="eval-baseline")
    candidate = attach_provenance({"score":0.74}, kind="eval-candidate")
    assert verify_provenance(baseline)[0]
    assert verify_provenance(candidate)[0]
    tampered = dict(candidate)
    tampered["score"] = 0.99
    assert not verify_provenance(tampered)[0]

    print("AEGIS LEARNING GATE TESTS PASS")

if __name__=="__main__": main()
