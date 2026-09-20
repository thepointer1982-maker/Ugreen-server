#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "aegis_service_lifecycle.py"

spec = importlib.util.spec_from_file_location("aegis_service_lifecycle", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def main() -> None:
    assert mod.version_tuple("v1.18.31") == (1, 18, 31)
    assert mod.version_tuple("codex-cli 0.155.1") == (0, 155, 1)
    assert mod.version_lt("0.9.9", "1.0.0") is True
    assert mod.version_lt("1.18.31", "1.17.0") is False
    assert mod.version_lt("2.337.0", "2.337.0") is False
    assert mod.version_lt("garbage", "1.0.0") is None

    manifest = json.loads(
        (ROOT / "config" / "lifecycle" / "aegis-services.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["schema"] == "aegis-service-lifecycle/v1"
    assert manifest["policy"]["auto_update_external_software"] is False
    assert manifest["policy"]["auto_remove_services"] is False
    assert manifest["policy"]["block_known_legacy_fallbacks"] is True

    by_id = {row["id"]: row for row in manifest["services"]}
    assert by_id["github-runner"]["known_current"] == "2.337.0"
    assert by_id["tailscale"]["known_current"] == "1.102.4"
    assert by_id["ollama"]["known_current"] == "0.34.2"
    assert by_id["codex"]["known_current"] == "0.155.1"
    assert by_id["mcp-python"]["known_current"] == "2.2.0"
    assert by_id["opencode"]["known_current"] == "1.18.31"
    assert by_id["opencode"]["legacy_below"] == "1.0.0"
    assert by_id["opencode"]["legacy_upstream"] == "opencode-ai/opencode"
    assert by_id["opencode"]["upstream"] == "anomalyco/opencode"

    coverage = {
        row["id"]: row["status"]
        for row in manifest["integration_coverage"]
    }
    assert coverage["iphone-shortcuts"] == "implemented"
    assert coverage["alexa-voice-context"] == "local-bridge-ready"
    assert coverage["opencode-local"] == "guarded-modern-conditional"
    assert coverage["tailscale-maintenance"] == "opt-in"
    assert coverage["deepseek-explicit-route"] == "implemented-conditional"
    assert coverage["astra-explicit-route"] == "not-local-core"
    assert coverage["mac-worker-wol"] == "implemented-unconfigured"
    assert coverage["offline-source-escrow"] == "implemented"
    assert coverage["windows-worker"] == "not-in-clean-branch"

    original_command_version = mod.command_version
    original_user_unit_state = mod.user_unit_state
    original_python_package_version = mod.python_package_version
    original_docker_container_version = mod.docker_container_version
    try:
        mod.command_version = lambda command, args: {
            "installed": True,
            "path": f"/usr/bin/{command}",
            "rc": 0,
            "version_raw": "1.18.31",
            "version": "1.18.31",
        }
        opencode = mod.inspect_service(by_id["opencode"])
        assert opencode["status"] == "healthy"
        assert opencode["legacy"] is False

        mod.command_version = lambda command, args: {
            "installed": True,
            "path": f"/usr/bin/{command}",
            "rc": 0,
            "version_raw": "0.9.4",
            "version": "0.9.4",
        }
        legacy = mod.inspect_service(by_id["opencode"])
        assert legacy["status"] == "legacy"
        assert "legacy-version" in legacy["reasons"]

        mod.user_unit_state = lambda unit: {
            "available": True,
            "load": "not-found",
            "active": "inactive",
        }
        missing = mod.inspect_service(by_id["pull-control"])
        assert missing["status"] == "blocked"
        assert "required-service-missing" in missing["reasons"]

        mod.docker_container_version = lambda name: {
            "installed": False,
            "reason": "container-not-found",
        }
        optional = mod.inspect_service(by_id["tailscale"])
        assert optional["status"] == "optional-missing"

        mod.python_package_version = lambda package, python_bin=None: {
            "installed": True,
            "version": "2.2.0",
            "version_raw": "2.2.0",
            "rc": 0,
        }
        mcp = mod.inspect_service(by_id["mcp-python"])
        assert mcp["status"] == "healthy"
    finally:
        mod.command_version = original_command_version
        mod.user_unit_state = original_user_unit_state
        mod.python_package_version = original_python_package_version
        mod.docker_container_version = original_docker_container_version

    installer = (
        ROOT / "scripts" / "aegis_service_lifecycle_install.sh"
    ).read_text(encoding="utf-8")
    assert "OnActiveSec=2min" in installer
    assert "OnUnitActiveSec=1d" in installer
    assert "NoNewPrivileges=true" in installer
    assert "ProtectSystem=full" in installer
    assert "ReadWritePaths=$STATE_DIR" in installer

    source = SCRIPT.read_text(encoding="utf-8")
    forbidden = [
        "apt upgrade",
        "apt-get upgrade",
        "dnf upgrade",
        "pacman -Syu",
        "docker pull",
        "docker rm",
        "docker rmi",
        "docker prune",
        "pip install",
        "npm install",
        "systemctl --user enable",
        "systemctl --user start",
    ]
    for token in forbidden:
        assert token not in source

    with tempfile.TemporaryDirectory() as raw:
        report = mod.attach_provenance(
            {
                "schema": "aegis-service-lifecycle-status/v1",
                "health": "healthy",
            },
            kind="service-lifecycle-status",
        )
        out = Path(raw) / "status.json"
        mod.atomic_json(out, report)
        loaded = mod.read_json(out)
        ok, reason = mod.verify_provenance(loaded)
        assert ok, reason

    print("AEGIS SERVICE LIFECYCLE TESTS PASS")


if __name__ == "__main__":
    main()
