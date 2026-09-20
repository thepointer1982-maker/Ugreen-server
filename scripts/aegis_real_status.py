#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aegis_last_known_good import active_status, current, provisional_status
from aegis_provenance import attach_provenance, verify_provenance


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def run(cmd: list[str], timeout: int = 20) -> dict[str, Any]:
    try:
        cp = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        return {
            "rc": cp.returncode,
            "stdout": cp.stdout[-4000:],
            "stderr": cp.stderr[-4000:],
        }
    except Exception as exc:
        return {"rc": 126, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}


def systemd_status(unit: str) -> dict[str, Any]:
    if not shutil_which("systemctl"):
        return {"available": False, "reason": "systemctl-missing"}

    user = run(["systemctl", "--user", "show", unit, "--no-page",
                "--property=LoadState,ActiveState,SubState,Result,ExecMainStatus"])
    if user["rc"] == 0 and "LoadState=" in user["stdout"]:
        return {"available": True, "scope": "user", **parse_systemd_show(user["stdout"])}

    system = run(["systemctl", "show", unit, "--no-page",
                  "--property=LoadState,ActiveState,SubState,Result,ExecMainStatus"])
    if system["rc"] == 0 and "LoadState=" in system["stdout"]:
        return {"available": True, "scope": "system", **parse_systemd_show(system["stdout"])}

    return {
        "available": True,
        "scope": None,
        "reason": "unit-not-found",
        "user_rc": user["rc"],
        "system_rc": system["rc"],
    }


def parse_systemd_show(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k] = v
    return out


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


def ollama_status() -> dict[str, Any]:
    result: dict[str, Any] = {
        "reachable": False,
        "endpoint": "http://127.0.0.1:11434/api/tags",
        "models": [],
    }
    try:
        req = urllib.request.Request(
            result["endpoint"],
            headers={"User-Agent": "AEGIS-real-status/1"},
        )
        with urllib.request.urlopen(req, timeout=2) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        result["reachable"] = True
        for item in data.get("models", []):
            if isinstance(item, dict):
                result["models"].append(item.get("name"))
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def file_state(path: Path, *, verify: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not result["exists"]:
        return result
    try:
        st = path.stat()
        result["size_bytes"] = st.st_size
        result["mtime"] = st.st_mtime
        result["age_seconds"] = max(0.0, time.time() - st.st_mtime)
    except OSError as exc:
        result["stat_error"] = str(exc)
    if verify:
        value = read_json(path)
        ok, reason = verify_provenance(value) if value else (False, "unreadable")
        result["provenance_verified"] = ok
        result["provenance_reason"] = reason
        if isinstance(value.get("_provenance"), dict):
            result["sha256"] = value["_provenance"].get("sha256")
    return result


def build_status(repo: Path) -> dict[str, Any]:
    home_state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    real_dir = Path(os.environ.get("AEGIS_REAL_CYCLE_STATE_DIR", home_state / "aegis-real-cycle"))
    ai_dir = Path(os.environ.get("AEGIS_AI_MINER_STATE_DIR", home_state / "aegis-ai-miner"))
    guardian_dir = Path(os.environ.get("AEGIS_GUARDIAN_STATE_DIR", home_state / "aegis-guardian"))
    scheduler_dir = Path(os.environ.get("AEGIS_SCHEDULER_STATE_DIR", home_state / "aegis-scheduler"))
    coder_boot_file = Path(
        os.environ.get(
            "AEGIS_CODER_BOOT_STATE_FILE",
            home_state / "aegis-coder-boot" / "status.json",
        )
    )
    codex_oss_file = Path(
        os.environ.get(
            "AEGIS_CODEX_OSS_STATE_FILE",
            home_state / "aegis-codex-oss" / "status.json",
        )
    )
    mcp_runtime_file = Path(
        os.environ.get(
            "AEGIS_MCP_STATE_FILE",
            home_state / "aegis-mcp" / "runtime.json",
        )
    )
    runner_state_file = Path(
        os.environ.get(
            "AEGIS_RUNNER_STATE_FILE",
            home_state / "aegis-runner" / "status.json",
        )
    )
    docker_efficiency_file = Path(
        os.environ.get(
            "AEGIS_DOCKER_EFFICIENCY_STATE_FILE",
            home_state / "aegis-docker-efficiency" / "status.json",
        )
    )
    autonomy_file = Path(
        os.environ.get(
            "AEGIS_AUTONOMY_STATUS_FILE",
            home_state / "aegis-autonomy" / "status.json",
        )
    )
    pull_control_file = Path(
        os.environ.get(
            "AEGIS_PULL_CONTROL_STATE_FILE",
            home_state / "aegis-pull-control" / "state.json",
        )
    )

    real = read_json(real_dir / "latest.json")
    ai = read_json(ai_dir / "latest.json")
    guardian = read_json(guardian_dir / "status.json")
    scheduler = {}
    coder_boot = read_json(coder_boot_file)
    codex_oss = read_json(codex_oss_file)
    mcp_runtime = read_json(mcp_runtime_file)
    runner_state = read_json(runner_state_file)
    docker_efficiency = read_json(docker_efficiency_file)
    autonomy = read_json(autonomy_file)
    pull_control = read_json(pull_control_file)
    env_path = scheduler_dir / "state.env"
    if env_path.is_file():
        for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in raw:
                k, v = raw.split("=", 1)
                scheduler[k] = v

    lkg = current()
    provisional = provisional_status()
    active = active_status()
    ollama = ollama_status()
    service = systemd_status("aegis-export.service")
    timer = systemd_status("aegis-export.timer")
    coder_timer = systemd_status("aegis-coder-boot.timer")
    runner_service = systemd_status("aegis-github-runner.service")
    docker_timer = systemd_status("aegis-docker-efficiency.timer")
    autonomy_timer = systemd_status("aegis-autonomy.timer")
    pull_control_timer = systemd_status("aegis-pull-control.timer")

    health = "healthy"
    blockers: list[str] = []

    if not real:
        health = "blocked"
        blockers.append("real-cycle-status-missing")
    elif real.get("status") != "healthy":
        health = "blocked"
        blockers.append(f"real-cycle-{real.get('status')}")

    if guardian and guardian.get("mode") != "healthy":
        health = "blocked"
        blockers.append(f"guardian-{guardian.get('mode')}")
    if ai:
        ok, _ = verify_provenance(ai)
        if not ok:
            health = "blocked"
            blockers.append("ai-provenance-invalid")

    if service.get("available") and service.get("LoadState") == "loaded":
        if service.get("ActiveState") not in {"inactive", "active"}:
            health = "degraded" if health == "healthy" else health
            blockers.append(f"service-{service.get('ActiveState')}")
    if timer.get("available") and timer.get("LoadState") == "loaded":
        if timer.get("ActiveState") != "active":
            health = "degraded" if health == "healthy" else health
            blockers.append(f"timer-{timer.get('ActiveState')}")

    coder_capability = "unknown"
    coder_selected = None
    coder_reason = "coder-boot-state-missing"
    if coder_boot:
        coder_selected = coder_boot.get("selected")
        coder_reason = coder_boot.get("reason")
        boot_health = coder_boot.get("health")
        if boot_health == "healthy":
            coder_capability = "ready"
        elif boot_health == "degraded":
            coder_capability = "fallback-ready"
            if health == "healthy":
                health = "degraded"
            blockers.append("coder-preferred-backend-unavailable")
        else:
            coder_capability = "blocked"
            if health == "healthy":
                health = "degraded"
            blockers.append("coder-backends-unavailable")

    mcp_health = mcp_runtime.get("health") if mcp_runtime else None
    runner_active = runner_state.get("service_active") if runner_state else None
    if mcp_runtime and mcp_health != "healthy":
        if health == "healthy":
            health = "degraded"
        blockers.append("mcp-runtime-unhealthy")
    pull_control_ok = False
    pull_control_reason = "missing"
    if pull_control:
        pull_control_ok, pull_control_reason = verify_provenance(pull_control)

    pull_timer_active = (
        pull_control_timer.get("available")
        and pull_control_timer.get("LoadState") == "loaded"
        and pull_control_timer.get("ActiveState") == "active"
    )
    primary_control_ready = bool(
        pull_timer_active
        and (
            not pull_control
            or (
                pull_control_ok
                and pull_control.get("transport") == "outbound-pull"
                and pull_control.get("runner_required") is False
            )
        )
    )

    if pull_control and not pull_control_ok:
        if health == "healthy":
            health = "degraded"
        blockers.append("pull-control-provenance-invalid")
    if (
        pull_control_timer.get("available")
        and pull_control_timer.get("LoadState") == "loaded"
        and pull_control_timer.get("ActiveState") != "active"
    ):
        if health == "healthy":
            health = "degraded"
        blockers.append("pull-control-timer-inactive")

    if runner_state and runner_active is not True and not primary_control_ready:
        if health == "healthy":
            health = "degraded"
        blockers.append("runner-service-inactive")

    if docker_efficiency:
        docker_ok, docker_reason = verify_provenance(docker_efficiency)
        if not docker_ok:
            if health == "healthy":
                health = "degraded"
            blockers.append("docker-efficiency-provenance-invalid")
        elif docker_efficiency.get("health") != "healthy":
            if health == "healthy":
                health = "degraded"
            blockers.append(
                f"docker-efficiency-{docker_efficiency.get('health')}"
            )

    if autonomy:
        autonomy_ok, _ = verify_provenance(autonomy)
        if not autonomy_ok:
            if health == "healthy":
                health = "degraded"
            blockers.append("autonomy-provenance-invalid")
        elif autonomy.get("health") not in {"healthy", "degraded"}:
            if health == "healthy":
                health = "degraded"
            blockers.append(
                f"autonomy-{autonomy.get('health')}"
            )
    elif health == "healthy":
        health = "degraded"
        blockers.append("autonomy-status-missing")

    return attach_provenance(
        {
            "schema": "aegis-real-status/v1",
            "generated_at": now_iso(),
            "repo": str(repo),
            "health": health,
            "blockers": blockers,
            "real_cycle": {
                "status": real.get("status"),
                "reason": real.get("reason"),
                "generated_at": real.get("generated_at"),
                "source_selection": real.get("sources"),
                "matrix": real.get("matrix"),
            },
            "guardian": {
                "mode": guardian.get("mode"),
                "reason": guardian.get("reason"),
                "generated_at": guardian.get("generated_at"),
                "network_score": guardian.get("evidence", {}).get("network_score")
                if isinstance(guardian.get("evidence"), dict) else None,
                "deepdiag_score": guardian.get("evidence", {}).get("deepdiag_score")
                if isinstance(guardian.get("evidence"), dict) else None,
                "evidence_verified": guardian.get("evidence", {}).get("evidence_verified")
                if isinstance(guardian.get("evidence"), dict) else None,
            },
            "scheduler": {
                "state": scheduler,
                "service": service,
                "timer": timer,
                "coder_boot_timer": coder_timer,
                "docker_efficiency_timer": docker_timer,
                "autonomy_timer": autonomy_timer,
                "pull_control_timer": pull_control_timer,
            },
            "control": {
                "primary": {
                    "transport": "outbound-pull",
                    "ready": primary_control_ready,
                    "runner_required": False,
                },
                "pull_control": {
                    "status": pull_control.get("status"),
                    "transport": pull_control.get("transport"),
                    "runner_required": pull_control.get("runner_required"),
                    "last_sequence": pull_control.get("last_sequence"),
                    "seen_sequence": pull_control.get("seen_sequence"),
                    "sequence": pull_control.get("sequence"),
                    "trusted_sha": pull_control.get("trusted_sha"),
                    "completed_at": pull_control.get("completed_at"),
                    "last_checked_at": pull_control.get("last_checked_at"),
                    "provenance_verified": pull_control_ok if pull_control else None,
                    "provenance_reason": pull_control_reason,
                    "systemd_timer": pull_control_timer,
                },
                "runner": {
                    "configured": runner_state.get("configured"),
                    "service_mode": runner_state.get("service_mode"),
                    "service_active": runner_state.get("service_active"),
                    "runner_name": runner_state.get("runner_name"),
                    "labels": runner_state.get("labels"),
                    "optional_fallback": True,
                    "systemd": runner_service,
                },
                "mcp": {
                    "health": mcp_runtime.get("health"),
                    "reason": mcp_runtime.get("reason"),
                    "transport": mcp_runtime.get("transport"),
                    "network_listener": mcp_runtime.get("network_listener"),
                    "public_port": mcp_runtime.get("public_port"),
                    "wrapper": mcp_runtime.get("wrapper"),
                },
                "docker": {
                    "health": docker_efficiency.get("health"),
                    "reason": docker_efficiency.get("reason"),
                    "full_inspect_performed": docker_efficiency.get(
                        "full_inspect_performed"
                    ),
                    "metrics": docker_efficiency.get("metrics"),
                    "guardrails": docker_efficiency.get("guardrails"),
                    "systemd_timer": docker_timer,
                },
                "autonomy": {
                    "profile": autonomy.get("profile"),
                    "health": autonomy.get("health"),
                    "reason": autonomy.get("reason"),
                    "action_count": autonomy.get("action_count"),
                    "queue": autonomy.get("queue"),
                    "consecutive_failed_cycles": autonomy.get(
                        "consecutive_failed_cycles"
                    ),
                    "policy": autonomy.get("policy"),
                    "systemd_timer": autonomy_timer,
                },
            },
            "ollama": {
                "reachable": ollama.get("reachable"),
                "model_count": len(ollama.get("models", []))
                if isinstance(ollama.get("models"), list) else 0,
                "models": ollama.get("models", []),
                "error": ollama.get("error"),
            },
            "coder": {
                "capability": coder_capability,
                "selected": coder_selected,
                "reason": coder_reason,
                "boot_state": coder_boot,
                "codex_oss": {
                    "health": codex_oss.get("health"),
                    "reason": codex_oss.get("reason"),
                    "provider": codex_oss.get("provider"),
                    "model": codex_oss.get("model"),
                    "ollama_ready": codex_oss.get("ollama_ready"),
                    "model_ready": codex_oss.get("model_ready"),
                    "cloud_model_usage": codex_oss.get("cloud_model_usage"),
                    "chatgpt_auth_required": codex_oss.get("chatgpt_auth_required"),
                    "openai_api_key_required": codex_oss.get("openai_api_key_required"),
                    "web_search": codex_oss.get("web_search"),
                    "shell_network_access": codex_oss.get("shell_network_access"),
                },
            },
            "learning": {
                "last_known_good": lkg.get("status"),
                "provisional": provisional.get("status"),
                "active": active.get("status"),
                "active_state": active.get("active", {}).get("state")
                if isinstance(active.get("active"), dict) else None,
            },
            "artifacts": {
                "real_cycle": file_state(real_dir / "latest.json", verify=True),
                "ai_miner": file_state(ai_dir / "latest.json", verify=True),
                "model_matrix": file_state(ai_dir / "model-agent-matrix.json", verify=True),
                "guardian": file_state(guardian_dir / "status.json"),
                "scheduler_state": file_state(env_path),
                "coder_boot": file_state(coder_boot_file),
                "codex_oss": file_state(codex_oss_file),
                "mcp_runtime": file_state(mcp_runtime_file),
                "runner": file_state(runner_state_file),
                "docker_efficiency": file_state(
                    docker_efficiency_file,
                    verify=True,
                ),
                "autonomy": file_state(
                    autonomy_file,
                    verify=True,
                ),
                "pull_control": file_state(
                    pull_control_file,
                    verify=True,
                ),
            },
        },
        kind="real-status",
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Aggregate verified AEGIS runtime status.")
    p.add_argument("--repo-root", type=Path, default=SCRIPT_DIR.parent)
    p.add_argument(
        "--output",
        type=Path,
        default=Path(
            os.environ.get(
                "AEGIS_REAL_STATUS_FILE",
                Path.home() / ".local/state/aegis-real-status/latest.json",
            )
        ),
    )
    args = p.parse_args()

    status = build_status(args.repo_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(
        json.dumps(status, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(args.output)
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0 if status["health"] == "healthy" else 2


if __name__ == "__main__":
    raise SystemExit(main())
