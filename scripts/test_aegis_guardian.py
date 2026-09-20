#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent / "aegis_guardian_cycle.py"
spec = importlib.util.spec_from_file_location("aegis_guardian_cycle", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def main() -> None:
    assert mod.classify(0, 0, 0) == ("healthy", "verified-cycle")
    assert mod.classify(3, 0, 7) == ("degraded", "persistent-failures")
    assert mod.classify(5, 0, 7) == ("emergency", "repeated-failures")
    assert mod.classify(0, 20, None) == ("blocked", "preflight-blocked")
    assert mod.parse_nonnegative_int("4", 0) == 4
    assert mod.parse_nonnegative_int("broken", 7) == 7
    assert mod.parse_nonnegative_int("-2", 3) == 3
    assert mod.probation_passes({
        "mode": "healthy",
        "evidence": {
            "evidence_verified": True,
            "local_ai_provenance_verified": True,
        },
    }) is True
    assert mod.probation_passes({
        "mode": "degraded",
        "evidence": {
            "evidence_verified": True,
            "local_ai_provenance_verified": True,
        },
    }) is False
    assert mod.probation_passes({
        "mode": "healthy",
        "evidence": {
            "evidence_verified": False,
            "local_ai_provenance_verified": True,
        },
    }) is False

    with tempfile.TemporaryDirectory() as raw:
        state = Path(raw)
        mod.STATE_DIR = state
        mod.CARDS_FILE = state / "learning-cards.jsonl"
        mod.INDEX_FILE = state / "learning-index.json"
        mod.LEARNING_LOCK = state / "learning.lock"
        a = mod.append_learning_card({"mode":"degraded","reason":"x","action":"observe","outcome":"degraded","rc":2})
        b = mod.append_learning_card({"mode":"degraded","reason":"x","action":"observe","outcome":"degraded","rc":2})
        assert a["recurrence"] == 1
        assert b["recurrence"] == 2
        rows = [json.loads(x) for x in mod.CARDS_FILE.read_text().splitlines()]
        assert len(rows) == 2
        assert rows[-1]["fingerprint"] == rows[0]["fingerprint"]

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        repo = root / "repo"
        repo.mkdir()
        state_dir = root / "bootstrap"
        state_dir.mkdir()
        report = state_dir / "preflight.json"
        report.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "status": "ready",
                    "repo_root": str(repo.resolve()),
                    "blockers": [],
                    "source": {"status": "ready"},
                    "git": {"is_repo": True},
                }
            ),
            encoding="utf-8",
        )
        old_reuse = os.environ.get("AEGIS_GUARDIAN_REUSE_PREFLIGHT")
        old_state = os.environ.get("AEGIS_STATE_DIR")
        os.environ["AEGIS_GUARDIAN_REUSE_PREFLIGHT"] = "1"
        os.environ["AEGIS_STATE_DIR"] = str(state_dir)
        try:
            reused = mod.reusable_preflight(repo)
            assert reused is not None
            assert reused.returncode == 0
            assert reused.args == ["reused-preflight"]
            assert '"reused": true' in reused.stdout.lower()

            payload = json.loads(report.read_text(encoding="utf-8"))
            payload["repo_root"] = str(root / "wrong")
            report.write_text(json.dumps(payload), encoding="utf-8")
            assert mod.reusable_preflight(repo) is None
        finally:
            if old_reuse is None:
                os.environ.pop("AEGIS_GUARDIAN_REUSE_PREFLIGHT", None)
            else:
                os.environ["AEGIS_GUARDIAN_REUSE_PREFLIGHT"] = old_reuse
            if old_state is None:
                os.environ.pop("AEGIS_STATE_DIR", None)
            else:
                os.environ["AEGIS_STATE_DIR"] = old_state

    with tempfile.TemporaryDirectory() as raw:
        cwd = Path(raw)
        cp = mod.run(["python3", "-c", "import time; time.sleep(2)"], cwd=cwd, timeout=1)
        assert cp.returncode == 124
        assert "timeout" in cp.stderr.lower()

    with tempfile.TemporaryDirectory() as raw:
        repo = Path(raw) / "repo"
        scheduler = Path(raw) / "scheduler"
        repo.mkdir()
        scheduler.mkdir()
        actions = mod.safe_repairs(repo, scheduler, allow_service_restart=False)
        assert not any(a.get("action") == "restart-aegis-export-service" for a in actions)

    print("AEGIS GUARDIAN TESTS PASS")


if __name__ == "__main__":
    main()
