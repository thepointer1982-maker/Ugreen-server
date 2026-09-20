#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, os, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory() as raw:
    os.environ["AEGIS_REPO_ROOT"]=str(ROOT)
    os.environ["AEGIS_PROJECT_STATE_DIR"]=str(Path(raw)/"project")
    os.environ["AEGIS_AUTONOMY_STATE_DIR"]=str(Path(raw)/"autonomy")
    os.environ["AEGIS_VOICE_STATE_DIR"]=str(Path(raw)/"voice")
    path=ROOT/"scripts"/"aegis_voice_bridge.py"
    spec=importlib.util.spec_from_file_location("voice",path)
    mod=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(mod)

    c=mod.handle_request({"op":"context","source":"alexa"})
    assert c["status"]=="ok"
    assert c["context"]["channel"]=="alexa"
    assert c["context"]["render_policy"]["no_style_reset"] is True

    q=mod.handle_request({"op":"enqueue","source":"alexa","text":"Prüfe den lokalen AEGIS Status"})
    assert q["status"]=="queued"
    assert q["context"]["project_id"]=="aegis-core"

    bad=mod.handle_request({"op":"enqueue","source":"internet","text":"x"})
    assert bad["status"]=="blocked"

    status=mod.bridge_status()
    assert status["transport"]=="unix-socket"
    assert status["network_listener"] is False
    assert status["public_port"] is False
    assert status["cloud_dependency_in_core"] is False

    install=(ROOT/"scripts"/"aegis_voice_bridge_install.sh").read_text(encoding="utf-8")
    assert "RestrictAddressFamilies=AF_UNIX" in install
    assert "NoNewPrivileges=true" in install
    assert "ProtectSystem=full" in install
    assert "listen" not in install.lower() or "tcp" not in install.lower()

print("AEGIS VOICE BRIDGE TESTS PASS")
