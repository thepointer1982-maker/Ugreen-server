#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "aegis_provenance.py"
spec = importlib.util.spec_from_file_location("aegis_provenance", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def main() -> None:
    base = {"a": 1, "b": {"x": 2}}
    signed = mod.attach_provenance(base, kind="test")
    ok, reason = mod.verify_provenance(signed)
    assert ok and reason == "verified"

    reordered = {"b": {"x": 2}, "a": 1, "_provenance": signed["_provenance"]}
    ok, reason = mod.verify_provenance(reordered)
    assert ok, reason

    tampered = dict(signed)
    tampered["a"] = 9
    ok, reason = mod.verify_provenance(tampered)
    assert not ok and reason == "sha256-mismatch"

    chained = mod.attach_provenance(
        {"score": 0.9},
        kind="child",
        parent_sha256=signed["_provenance"]["sha256"],
        parent_kind="test",
    )
    assert chained["_provenance"]["parent_sha256"] == signed["_provenance"]["sha256"]
    print("AEGIS PROVENANCE TESTS PASS")


if __name__ == "__main__":
    main()
