#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aegis_provenance import attach_provenance, verify_provenance

REPO_ROOT = Path(
    os.environ.get("AEGIS_REPO_ROOT", Path(__file__).resolve().parents[1])
).resolve()
POLICY_FILE = Path(
    os.environ.get(
        "AEGIS_AUTONOMY_POLICY",
        REPO_ROOT / "config" / "autonomy" / "aegis-max-local.json",
    )
)
STATE_DIR = Path(
    os.environ.get(
        "AEGIS_AUTONOMY_STATE_DIR",
        Path.home() / ".local" / "state" / "aegis-autonomy",
    )
)
STATUS_FILE = STATE_DIR / "status.json"
QUEUE_FILE = STATE_DIR / "tasks.jsonl"
ACTION_LOG = STATE_DIR / "actions.jsonl"
DOCKER_STATE = Path(
    os.environ.get(
        "AEGIS_DOCKER_EFFICIENCY_STATE_FILE",
        Path.home() / ".local/state/aegis-docker-efficiency/status.json",
    )
)
PROJECT_CONTEXT = Path(
    os.environ.get(
        "AEGIS_PROJECT_CONTEXT_FILE",
        Path.home() / ".local/state/aegis-project/context.json",
    )
)
CODER_ROUTER = REPO_ROOT / "scripts" / "aegis_coder_router.sh"
GUARDIAN = REPO_ROOT / "scripts" / "aegis_guardian_cycle.py"

MAX_TASK_LEN = 4000
MAX_QUEUE_SCAN = 200
ALLOWED_PRIORITIES = {"low": 10, "normal": 50, "high": 80, "critical": 100}


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    finally:
        try:
            os.unlink(raw)
        except FileNotFoundError:
            pass


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def run(
    cmd: list[str],
    *,
    timeout: int = 120,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            cmd,
            124,
            exc.stdout if isinstance(exc.stdout, str) else "",
            exc.stderr if isinstance(exc.stderr, str) else "timeout",
        )
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 126, "", str(exc))


def load_policy() -> dict[str, Any]:
    policy = read_json(POLICY_FILE)
    if policy.get("schema") != "aegis-autonomy-policy/v1":
        raise RuntimeError("invalid autonomy policy schema")
    if policy.get("profile") != "AUTO-MAX-LOCAL":
        raise RuntimeError("unexpected autonomy profile")
    if policy.get("recurring_cloud_ai_cost_allowed") is not False:
        raise RuntimeError("cloud-cost guardrail must remain disabled")
    return policy


def policy_summary(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": policy.get("profile"),
        "revision": policy.get("revision"),
        "local_first": policy.get("local_first"),
        "recurring_cloud_ai_cost_allowed": policy.get(
            "recurring_cloud_ai_cost_allowed"
        ),
        "automatic_capabilities": policy.get("automatic_capabilities", {}),
        "hard_blocks": policy.get("hard_blocks", []),
    }


def systemd_user_state(unit: str) -> dict[str, Any]:
    cp = run(
        [
            "systemctl",
            "--user",
            "show",
            unit,
            "--no-page",
            "--property=LoadState,ActiveState,SubState,Result",
        ],
        timeout=10,
    )
    if cp.returncode != 0:
        return {"load": "unknown", "active": "unknown", "rc": cp.returncode}
    values: dict[str, str] = {}
    for line in cp.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return {
        "load": values.get("LoadState"),
        "active": values.get("ActiveState"),
        "sub": values.get("SubState"),
        "result": values.get("Result"),
        "rc": cp.returncode,
    }


def ensure_user_units(
    policy: dict[str, Any],
    max_actions: int,
) -> tuple[list[dict[str, Any]], int]:
    actions: list[dict[str, Any]] = []
    used = 0
    units = policy.get("allowlisted_user_units", [])
    if not isinstance(units, list):
        return actions, used

    for unit in units:
        if used >= max_actions:
            break
        if unit == "aegis-autonomy.timer":
            continue
        if not isinstance(unit, str) or not unit.startswith("aegis-"):
            continue
        state = systemd_user_state(unit)
        if state.get("load") != "loaded":
            continue
        if state.get("active") == "active":
            continue

        cp = run(["systemctl", "--user", "start", unit], timeout=30)
        action = {
            "action": "start-user-unit",
            "target": unit,
            "ok": cp.returncode == 0,
            "rc": cp.returncode,
            "at": now_iso(),
        }
        actions.append(action)
        used += 1
    return actions, used


def run_guardian_repair() -> dict[str, Any]:
    if not GUARDIAN.is_file():
        return {"action": "guardian-repair", "ok": False, "reason": "missing"}
    cp = run(
        [
            sys.executable,
            str(GUARDIAN),
            "--repo-root",
            str(REPO_ROOT),
            "--repair",
            "--observe-only",
        ],
        timeout=300,
        cwd=REPO_ROOT,
    )
    payload: dict[str, Any] = {
        "action": "guardian-repair",
        "ok": cp.returncode in {0, 2},
        "rc": cp.returncode,
        "at": now_iso(),
    }
    try:
        parsed = json.loads(cp.stdout)
        if isinstance(parsed, dict):
            payload["mode"] = parsed.get("mode")
            payload["reason"] = parsed.get("reason")
            payload["repairs"] = parsed.get("repairs")
    except Exception:
        pass
    return payload


def _docker_restart_state(previous: dict[str, Any]) -> dict[str, Any]:
    value = previous.get("docker_restart_state")
    return value if isinstance(value, dict) else {}


def _container_anomalous(row: dict[str, Any]) -> bool:
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    return (
        str(details.get("health") or row.get("ps_health") or "") == "unhealthy"
        or bool(details.get("dead"))
    )


def _docker_label_allows(container_id: str, required: str) -> bool:
    key, expected = required.split("=", 1)
    cp = run(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .Config.Labels}}",
            container_id,
        ],
        timeout=10,
    )
    if cp.returncode != 0:
        return False
    try:
        labels = json.loads(cp.stdout)
    except Exception:
        return False
    return isinstance(labels, dict) and str(labels.get(key, "")).lower() == expected.lower()


def maybe_restart_opted_in_docker(
    policy: dict[str, Any],
    previous: dict[str, Any],
    max_actions: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    state = _docker_restart_state(previous)
    docker = read_json(DOCKER_STATE)
    ok, _ = verify_provenance(docker) if docker else (False, "missing")
    if not ok:
        return actions, state

    config = policy.get("docker_autorepair", {})
    if not isinstance(config, dict):
        return actions, state

    required = str(config.get("required_label") or "aegis.autorepair=true")
    min_bad = int(config.get("minimum_consecutive_unhealthy_checks") or 3)
    cooldown = int(config.get("cooldown_seconds") or 900)
    max_per_cycle = int(config.get("max_restarts_per_container_per_hour") or 1)
    rows = docker.get("containers", [])
    if not isinstance(rows, list):
        return actions, state

    current_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "")
        if not cid:
            continue
        current_ids.add(cid)
        entry = state.get(cid)
        if not isinstance(entry, dict):
            entry = {
                "consecutive_bad": 0,
                "last_restart_epoch": 0.0,
                "restart_epochs": [],
            }

        if _container_anomalous(row):
            entry["consecutive_bad"] = int(entry.get("consecutive_bad") or 0) + 1
        else:
            entry["consecutive_bad"] = 0

        epochs = [
            float(x)
            for x in entry.get("restart_epochs", [])
            if isinstance(x, (int, float))
            and time.time() - float(x) < 3600
        ]
        entry["restart_epochs"] = epochs

        eligible = (
            len(actions) < max_actions
            and entry["consecutive_bad"] >= min_bad
            and time.time() - float(entry.get("last_restart_epoch") or 0) >= cooldown
            and len(epochs) < max_per_cycle
        )
        if eligible and _docker_label_allows(cid, required):
            cp = run(["docker", "restart", "--time", "10", cid], timeout=60)
            event = {
                "action": "restart-opted-in-docker",
                "target": cid,
                "name": row.get("name"),
                "ok": cp.returncode == 0,
                "rc": cp.returncode,
                "at": now_iso(),
            }
            actions.append(event)
            if cp.returncode == 0:
                stamp = time.time()
                entry["last_restart_epoch"] = stamp
                entry["restart_epochs"] = epochs + [stamp]
                entry["consecutive_bad"] = 0

        state[cid] = entry

    for cid in list(state):
        if cid not in current_ids:
            state.pop(cid, None)

    return actions, state


def queue_task(
    task: str,
    *,
    source: str = "mcp",
    priority: str = "normal",
) -> dict[str, Any]:
    text = str(task).strip()
    if not text or len(text) > MAX_TASK_LEN:
        return {"status": "blocked", "reason": "invalid-task-length"}
    if priority not in ALLOWED_PRIORITIES:
        return {"status": "blocked", "reason": "invalid-priority"}

    item = {
        "schema": "aegis-autonomy-task/v1",
        "task_id": str(uuid.uuid4()),
        "created_at": now_iso(),
        "source": str(source)[:100],
        "priority": priority,
        "priority_value": ALLOWED_PRIORITIES[priority],
        "status": "queued",
        "task": text,
    }
    append_jsonl(QUEUE_FILE, item)
    return {"status": "queued", "task_id": item["task_id"]}


def load_queue() -> list[dict[str, Any]]:
    if not QUEUE_FILE.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in QUEUE_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-MAX_QUEUE_SCAN:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def write_queue(rows: list[dict[str, Any]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix="tasks.", dir=str(STATE_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for item in rows[-MAX_QUEUE_SCAN:]:
                handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, QUEUE_FILE)
    finally:
        try:
            os.unlink(raw)
        except FileNotFoundError:
            pass


def process_one_local_ai_task(policy: dict[str, Any]) -> dict[str, Any] | None:
    caps = policy.get("automatic_capabilities", {})
    if not isinstance(caps, dict) or not caps.get("run_tests_and_validation"):
        return None
    if not CODER_ROUTER.is_file():
        return None

    rows = load_queue()
    candidates = [
        item
        for item in rows
        if item.get("schema") == "aegis-autonomy-task/v1"
        and item.get("status") == "queued"
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            -int(item.get("priority_value") or 0),
            str(item.get("created_at") or ""),
        )
    )
    selected = candidates[0]
    task_id = selected.get("task_id")
    env = os.environ.copy()
    env["AEGIS_ALLOW_CLOUD_CODEX"] = "0"
    env["AEGIS_PROJECT_CONTEXT_REQUIRED"] = "1"

    cp = run(
        ["bash", str(CODER_ROUTER), "auto", str(selected.get("task") or "")],
        timeout=1800,
        cwd=REPO_ROOT,
        env=env,
    )
    result = {
        "action": "local-ai-task",
        "task_id": task_id,
        "ok": cp.returncode == 0,
        "rc": cp.returncode,
        "at": now_iso(),
        "backend": "local-coder-router",
    }

    for item in rows:
        if item.get("task_id") == task_id:
            item["status"] = "completed" if cp.returncode == 0 else "failed"
            item["completed_at"] = now_iso()
            item["returncode"] = cp.returncode
            break
    write_queue(rows)
    return result


def circuit_open(previous: dict[str, Any], policy: dict[str, Any]) -> tuple[bool, float]:
    failures = int(previous.get("consecutive_failed_cycles") or 0)
    config = policy.get("cycle", {})
    threshold = int(config.get("max_consecutive_failed_cycles") or 3)
    breaker = int(config.get("circuit_breaker_seconds") or 900)
    opened_at = float(previous.get("circuit_opened_epoch") or 0)
    if failures < threshold:
        return False, 0.0
    remaining = breaker - (time.time() - opened_at)
    return remaining > 0, max(0.0, remaining)


def run_cycle() -> dict[str, Any]:
    policy = load_policy()
    previous = read_json(STATUS_FILE)
    open_now, remaining = circuit_open(previous, policy)
    if open_now:
        status = attach_provenance(
            {
                "schema": "aegis-autonomy-status/v1",
                "generated_at": now_iso(),
                "profile": policy.get("profile"),
                "health": "degraded",
                "reason": "circuit-breaker-open",
                "circuit_remaining_seconds": round(remaining, 1),
                "actions": [],
                "policy": policy_summary(policy),
                "consecutive_failed_cycles": int(
                    previous.get("consecutive_failed_cycles") or 0
                ),
                "circuit_opened_epoch": float(
                    previous.get("circuit_opened_epoch") or time.time()
                ),
                "docker_restart_state": _docker_restart_state(previous),
            },
            kind="autonomy-status",
        )
        atomic_json(STATUS_FILE, status)
        return status

    max_actions = int(policy.get("cycle", {}).get("max_actions_per_cycle") or 4)
    actions: list[dict[str, Any]] = []

    unit_actions, used = ensure_user_units(policy, max_actions)
    actions.extend(unit_actions)

    if len(actions) < max_actions:
        guardian = run_guardian_repair()
        actions.append(guardian)

    docker_actions, docker_restart_state = maybe_restart_opted_in_docker(
        policy,
        previous,
        max(0, max_actions - len(actions)),
    )
    actions.extend(docker_actions)

    ai_task = None
    if len(actions) < max_actions:
        ai_task = process_one_local_ai_task(policy)
        if ai_task is not None:
            actions.append(ai_task)

    hard_failures = [
        action
        for action in actions
        if action.get("ok") is False
        and action.get("action") in {
            "guardian-repair",
            "start-user-unit",
            "restart-opted-in-docker",
        }
    ]
    consecutive = 0 if not hard_failures else int(
        previous.get("consecutive_failed_cycles") or 0
    ) + 1
    threshold = int(
        policy.get("cycle", {}).get("max_consecutive_failed_cycles") or 3
    )
    circuit_epoch = 0.0
    if consecutive >= threshold:
        circuit_epoch = time.time()

    for action in actions:
        append_jsonl(ACTION_LOG, action)

    health = "healthy" if not hard_failures else "degraded"
    status = attach_provenance(
        {
            "schema": "aegis-autonomy-status/v1",
            "generated_at": now_iso(),
            "profile": policy.get("profile"),
            "health": health,
            "reason": "cycle-complete" if health == "healthy" else "action-failure",
            "actions": actions,
            "action_count": len(actions),
            "policy": policy_summary(policy),
            "consecutive_failed_cycles": consecutive,
            "circuit_opened_epoch": circuit_epoch,
            "docker_restart_state": docker_restart_state,
            "queue": {
                "queued": len(
                    [
                        x
                        for x in load_queue()
                        if x.get("status") == "queued"
                    ]
                ),
                "processed_task": ai_task.get("task_id") if ai_task else None,
            },
        },
        kind="autonomy-status",
    )
    atomic_json(STATUS_FILE, status)
    return status


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cycle")
    sub.add_parser("status")
    add = sub.add_parser("enqueue")
    add.add_argument("--task", required=True)
    add.add_argument("--source", default="cli")
    add.add_argument("--priority", default="normal")
    args = p.parse_args()

    if args.cmd == "cycle":
        status = run_cycle()
        print(json.dumps(status, indent=2, ensure_ascii=False))
        return 0 if status.get("health") in {"healthy", "degraded"} else 2
    if args.cmd == "status":
        print(json.dumps(read_json(STATUS_FILE), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "enqueue":
        result = queue_task(
            args.task,
            source=args.source,
            priority=args.priority,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("status") == "queued" else 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
