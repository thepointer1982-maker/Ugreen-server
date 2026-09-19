#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aegis_provenance import attach_provenance

SCRIPT = SCRIPT_DIR / "aegis_last_known_good.py"
spec = importlib.util.spec_from_file_location("aegis_last_known_good", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        mod.ROOT = root / "lkg"
        mod.VERSIONS = mod.ROOT / "versions"
        mod.POINTER = mod.ROOT / "last-known-good.json"
        mod.HISTORY = mod.ROOT / "history.jsonl"

        src = root / "candidate.json"
        src.write_text(
            json.dumps(attach_provenance({"score": 0.91}, kind="eval-candidate")),
            encoding="utf-8",
        )

        result = mod.promote(src, kind="model-routing")
        assert result["status"] == "promoted"
        cur = mod.current()
        assert cur["status"] == "ok"
        digest = cur["pointer"]["sha256"]

        dest = root / "active.json"
        restored = mod.restore(dest)
        assert restored["status"] == "restored"
        assert json.loads(dest.read_text())["_provenance"]["sha256"] == digest

        tampered = json.loads(src.read_text())
        tampered["score"] = 0.01
        src.write_text(json.dumps(tampered), encoding="utf-8")
        blocked = mod.promote(src, kind="model-routing")
        assert blocked["status"] == "blocked"

    print("AEGIS LAST KNOWN GOOD TESTS PASS")


if __name__ == "__main__":
    main()
