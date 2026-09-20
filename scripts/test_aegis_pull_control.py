#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "aegis_pull_control.py"


def load_module():
    spec = importlib.util.spec_from_file_location("aegis_pull_control", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def main():
    mod = load_module()
    req = {
        "schema": "aegis-control/v2",
        "action": "status",
        "sequence": 5,
        "trusted_sha": "a" * 40,
    }
    action, seq, sha = mod.validate_request(req, 4)
    assert (action, seq, sha) == ("status", 5, "a" * 40)
    action, seq, stale = mod.validate_request(req, 5)
    assert stale == "stale"

    bad = dict(req)
    bad["action"] = "shell"
    try:
        mod.validate_request(bad, 0)
        raise AssertionError("shell action should be blocked")
    except RuntimeError:
        pass

    bad = dict(req)
    bad["trusted_sha"] = "bad"
    try:
        mod.validate_request(bad, 0)
        raise AssertionError("bad sha should be blocked")
    except RuntimeError:
        pass

    head = "b" * 40
    assert mod.can_fast_idle(
        {
            "status": "success",
            "last_sequence": 5,
            "seen_sequence": 5,
            "control_head": head,
        },
        head,
        5,
    )
    assert mod.can_fast_idle(
        {
            "status": "idle",
            "last_sequence": 5,
            "seen_sequence": 5,
            "control_head": head,
        },
        head,
        5,
    )
    assert not mod.can_fast_idle(
        {
            "status": "failed",
            "last_sequence": 4,
            "seen_sequence": 5,
            "control_head": head,
        },
        head,
        4,
    )
    assert not mod.can_fast_idle(
        {
            "status": "idle",
            "last_sequence": 5,
            "seen_sequence": 5,
            "control_head": "c" * 40,
        },
        head,
        5,
    )

    source = SCRIPT.read_text(encoding="utf-8")
    assert '"git", "ls-remote", "--heads", "origin", CONTROL_HEAD' in source
    assert '"git", "fetch", "--no-tags"' in source
    assert '"git", "fetch", "--prune", "origin"' not in source
    assert "fetch_control_ref(repo)" in source
    assert "fetch_dev_ref(repo)" in source
    assert "can_fast_idle" in source
    assert "aegis/resume-pre-lenovo-20260920" in source
    assert 'merge-base", "--is-ancestor"' in source
    assert '"shell"' not in mod.ALLOWED

    installer = SCRIPT.parent / "aegis_pull_control_install.sh"
    install_text = installer.read_text(encoding="utf-8")
    assert 'ReadWritePaths="$REPO_ROOT"' in install_text
    assert "OnBootSec=20s" in install_text
    assert "OnUnitActiveSec=60s" in install_text
    assert "AccuracySec=10s" in install_text
    assert "ProtectHome=read-only" in install_text

    with tempfile.TemporaryDirectory() as raw:
        p = Path(raw) / "state.json"
        unsigned = {
            "last_sequence": 7,
            "seen_sequence": 7,
            "control_head": head,
            "status": "idle",
        }
        mod.atomic_write_json(p, unsigned)
        rejected = mod.read_state(p)
        assert rejected == {"last_sequence": 0}

        signed = mod.write_state(
            p,
            {
                **unsigned,
                "transport": "outbound-pull",
                "runner_required": False,
            },
        )
        state = mod.read_state(p)
        assert state["last_sequence"] == 7
        assert state["control_head"] == head
        assert state["transport"] == "outbound-pull"
        assert state["runner_required"] is False
        ok, reason = mod.verify_provenance(state)
        assert ok, reason
        assert state["_provenance"]["kind"] == "pull-control-state"
        assert signed["_provenance"]["sha256"] == state["_provenance"]["sha256"]

    print("AEGIS PULL CONTROL TESTS PASS")


if __name__ == "__main__":
    main()
