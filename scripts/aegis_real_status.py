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

    real = read_json(real_dir / "latest.json")
    ai = read_json(ai_dir / "latest.json")
    guardian = read_json(guardian_dir / "status.json")
    scheduler = {}
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
            },
            "ollama": {
                "reachable": ollama.get("reachable"),
                "model_count": len(ollama.get("models", []))
                if isinstance(ollama.get("models"), list) else 0,
                "models": ollama.get("models", []),
                "error": ollama.get("error"),
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
