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
    req = {"schema":"aegis-control/v2","action":"status","sequence":5,"trusted_sha":"a"*40}
    action, seq, sha = mod.validate_request(req, 4)
    assert (action, seq, sha) == ("status", 5, "a"*40)
    action, seq, stale = mod.validate_request(req, 5)
    assert stale == "stale"
    bad = dict(req); bad["action"] = "shell"
    try:
        mod.validate_request(bad, 0)
        raise AssertionError("shell action should be blocked")
    except RuntimeError:
        pass
    bad = dict(req); bad["trusted_sha"] = "bad"
    try:
        mod.validate_request(bad, 0)
        raise AssertionError("bad sha should be blocked")
    except RuntimeError:
        pass
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"git", "fetch", "--prune", "origin"' in source
    assert 'merge-base", "--is-ancestor"' in source
    assert '"shell"' not in mod.ALLOWED

    installer = SCRIPT.parent / "aegis_pull_control_install.sh"
    install_text = installer.read_text(encoding="utf-8")
    assert 'ReadWritePaths="$REPO_ROOT"' in install_text
    assert "OnUnitActiveSec=5min" in install_text
    assert "ProtectHome=read-only" in install_text

    with tempfile.TemporaryDirectory() as raw:
        p = Path(raw) / "state.json"
        mod.atomic_write_json(p, {"last_sequence": 7})
        assert mod.read_state(p)["last_sequence"] == 7
    print("AEGIS PULL CONTROL TESTS PASS")

if __name__ == "__main__":
    main()
