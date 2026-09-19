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
        mod.PROVISIONAL = mod.ROOT / "provisional.json"
        mod.PROBATION_HISTORY = mod.ROOT / "probation-history.jsonl"
        mod.ACTIVE = mod.ROOT / "active.json"
        mod.PREVIOUS_ACTIVE = mod.ROOT / "previous-active.json"
        mod.ACTIVE_HISTORY = mod.ROOT / "active-history.jsonl"
        mod.ACTIVE_BACKUPS = mod.ROOT / "active-backups"

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

        candidate2 = root / "candidate2.json"
        candidate2.write_text(
            json.dumps(attach_provenance({"score": 0.95}, kind="eval-candidate")),
            encoding="utf-8",
        )
        staged = mod.stage_provisional(
            candidate2,
            kind="model-routing",
            required_passes=2,
            max_failures=1,
        )
        assert staged["status"] == "provisional"
        assert mod.current()["pointer"]["sha256"] == digest
        probation1 = mod.observe_provisional(passed=True, evidence={"run": 1})
        assert probation1["status"] == "probation"
        probation2 = mod.observe_provisional(passed=True, evidence={"run": 2})
        assert probation2["status"] == "confirmed"
        new_digest = mod.current()["pointer"]["sha256"]
        assert new_digest != digest

        allowed_root = root / "allowed"
        mod.DEFAULT_RESTORE_ROOTS = [allowed_root]
        dest = allowed_root / "active.json"
        restored = mod.restore(dest)
        assert restored["status"] == "restored"
        assert json.loads(dest.read_text())["_provenance"]["sha256"] == new_digest

        candidate3 = root / "candidate3.json"
        candidate3.write_text(
            json.dumps(attach_provenance({"score": 0.96}, kind="eval-candidate")),
            encoding="utf-8",
        )
        staged2 = mod.stage_provisional(
            candidate3,
            kind="model-routing",
            required_passes=3,
            max_failures=0,
        )
        assert staged2["status"] == "provisional"
        rejected = mod.observe_provisional(
            passed=False,
            evidence={"run": "regression"},
            rollback_destination=dest,
        )
        assert rejected["status"] == "rejected"
        assert rejected["rollback"]["status"] in {"restored", "already-current"}

        active_target = allowed_root / "routing.json"
        first_active = mod.activate_current_lkg(active_target, required_health_passes=2)
        assert first_active["status"] == "activated-validating"
        h1 = mod.observe_active_health(passed=True, evidence={"run": 1})
        assert h1["status"] == "validating"
        h2 = mod.observe_active_health(passed=True, evidence={"run": 2})
        assert h2["status"] == "stable"

        candidate4 = root / "candidate4.json"
        candidate4.write_text(
            json.dumps(attach_provenance({"score": 0.99}, kind="eval-candidate")),
            encoding="utf-8",
        )
        promoted4 = mod.promote(candidate4, kind="model-routing")
        assert promoted4["status"] == "promoted"
        second_active = mod.activate_current_lkg(active_target, required_health_passes=2)
        assert second_active["status"] == "activated-validating"
        bad_health = mod.observe_active_health(
            passed=False,
            evidence={"run": "activation-regression"},
        )
        assert bad_health["status"] == "regression"
        assert bad_health["rollback"]["status"] == "rolled-back"
        rolled_value = json.loads(active_target.read_text())
        assert rolled_value["_provenance"]["sha256"] == new_digest

        outside = root / "outside" / "active.json"
        blocked_restore = mod.restore(outside)
        assert blocked_restore["status"] == "blocked"
        assert blocked_restore["reason"] == "destination-outside-allowlist"

        tampered = json.loads(src.read_text())
        tampered["score"] = 0.01
        src.write_text(json.dumps(tampered), encoding="utf-8")
        blocked = mod.promote(src, kind="model-routing")
        assert blocked["status"] == "blocked"

    print("AEGIS LAST KNOWN GOOD TESTS PASS")


if __name__ == "__main__":
    main()
