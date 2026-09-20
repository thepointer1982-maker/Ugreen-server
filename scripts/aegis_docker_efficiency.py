#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aegis_provenance import attach_provenance, verify_provenance

STATE_DIR = Path(
    os.environ.get(
        "AEGIS_DOCKER_EFFICIENCY_STATE_DIR",
        Path.home() / ".local/state/aegis-docker-efficiency",
    )
)
STATUS_FILE = STATE_DIR / "status.json"
COMPOSE_ROOT = Path(os.environ.get("AEGIS_DOCKER_COMPOSE_ROOT", "/volume1/docker"))
FULL_INSPECT_SECONDS = int(os.environ.get("AEGIS_DOCKER_FULL_INSPECT_SECONDS", "900"))
MAX_COMPOSE_FILES = int(os.environ.get("AEGIS_DOCKER_MAX_COMPOSE_FILES", "250"))
PRUNE_DIRS = {
    ".git", ".cache", "cache", "node_modules",
    ".venv", "venv", "__pycache__",
}
COMPOSE_NAMES = {
    "docker-compose.yml", "docker-compose.yaml",
    "compose.yml", "compose.yaml",
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat()


def run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
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


def load_previous() -> dict[str, Any]:
    try:
        value = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(value, dict):
        return {}
    ok, _ = verify_provenance(value)
    return value if ok else {}


def parse_ps(raw: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("ID"):
            rows.append(value)
    return rows


def health_token(status: str) -> str:
    text = status.lower()
    if "unhealthy" in text:
        return "unhealthy"
    if "health: starting" in text:
        return "starting"
    if "healthy" in text:
        return "healthy"
    if "restarting" in text:
        return "restarting"
    if "dead" in text:
        return "dead"
    return "none"


def ps_fingerprint(row: dict[str, Any]) -> str:
    stable = {
        "id": row.get("ID"),
        "image": row.get("Image"),
        "state": row.get("State"),
        "name": row.get("Names"),
        "ports": row.get("Ports"),
        "networks": row.get("Networks"),
        "health": health_token(str(row.get("Status") or "")),
    }
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def inspect_summary(item: dict[str, Any]) -> dict[str, Any]:
    state = item.get("State") if isinstance(item.get("State"), dict) else {}
    health = state.get("Health") if isinstance(state.get("Health"), dict) else {}
    host = item.get("HostConfig") if isinstance(item.get("HostConfig"), dict) else {}
    restart = host.get("RestartPolicy") if isinstance(host.get("RestartPolicy"), dict) else {}
    log_cfg = host.get("LogConfig") if isinstance(host.get("LogConfig"), dict) else {}
    config = item.get("Config") if isinstance(item.get("Config"), dict) else {}
    return {
        "id": item.get("Id"),
        "name": str(item.get("Name") or "").lstrip("/"),
        "image": config.get("Image"),
        "image_id": item.get("Image"),
        "status": state.get("Status"),
        "running": state.get("Running"),
        "restarting": state.get("Restarting"),
        "dead": state.get("Dead"),
        "oom_killed": state.get("OOMKilled"),
        "exit_code": state.get("ExitCode"),
        "health": health.get("Status"),
        "restart_count": item.get("RestartCount"),
        "restart_policy": restart.get("Name"),
        "log_driver": log_cfg.get("Type"),
        "mount_count": len(item.get("Mounts") or [])
        if isinstance(item.get("Mounts"), list)
        else None,
    }


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def full_inspect_due(previous: dict[str, Any]) -> bool:
    last = parse_time(previous.get("last_full_inspect_at"))
    if last is None:
        return True
    return (now() - last).total_seconds() >= FULL_INSPECT_SECONDS


def compose_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        keep: list[str] = []
        for name in dirnames:
            candidate = Path(dirpath) / name
            if name in PRUNE_DIRS:
                continue
            try:
                if candidate.is_symlink():
                    continue
            except OSError:
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            if name in COMPOSE_NAMES:
                found.append(Path(dirpath) / name)
                if len(found) >= MAX_COMPOSE_FILES:
                    return found
    return found


def file_identity(path: Path) -> dict[str, Any]:
    try:
        st = path.stat()
        return {
            "path": str(path),
            "size_bytes": st.st_size,
            "mtime_ns": st.st_mtime_ns,
        }
    except OSError:
        return {"path": str(path), "size_bytes": None, "mtime_ns": None}


def compose_command() -> list[str] | None:
    cp = run(["docker", "compose", "version"], timeout=5)
    if cp.returncode == 0:
        return ["docker", "compose"]
    cp = run(["docker-compose", "version"], timeout=5)
    if cp.returncode == 0:
        return ["docker-compose"]
    return None


def validate_compose(
    files: list[Path],
    previous: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    old = previous.get("compose")
    prev = {
        str(row.get("path")): row
        for row in old
        if isinstance(row, dict) and row.get("path")
    } if isinstance(old, list) else {}

    command = compose_command()
    rows: list[dict[str, Any]] = []
    stats = {"cache_hits": 0, "validated": 0, "unavailable": 0}

    for path in files:
        identity = file_identity(path)
        cached = prev.get(str(path))
        if (
            isinstance(cached, dict)
            and cached.get("size_bytes") == identity.get("size_bytes")
            and cached.get("mtime_ns") == identity.get("mtime_ns")
        ):
            row = dict(cached)
            row["cache_reused"] = True
            rows.append(row)
            stats["cache_hits"] += 1
            continue

        row = {**identity, "cache_reused": False}
        if command is None:
            row.update({"valid": None, "rc": None, "reason": "compose-unavailable"})
            stats["unavailable"] += 1
        else:
            cp = run(command + ["-f", str(path), "config", "-q"], timeout=30)
            row.update({"valid": cp.returncode == 0, "rc": cp.returncode})
            stats["validated"] += 1
        rows.append(row)

    return rows, stats


def blocked_report(reason: str) -> dict[str, Any]:
    return attach_provenance(
        {
            "schema": "aegis-docker-efficiency/v1",
            "generated_at": now_iso(),
            "health": "blocked",
            "reason": reason,
            "guardrails": {
                "read_only": True,
                "docker_pull": False,
                "docker_prune": False,
                "docker_restart": False,
                "registry_queries": False,
                "host_tuning": False,
                "volume_mutation": False,
            },
        },
        kind="docker-efficiency",
    )


def main() -> int:
    previous = load_previous()
    version = run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        timeout=5,
    )
    if version.returncode != 0:
        report = blocked_report("docker-unavailable")
        atomic_json(STATUS_FILE, report)
        print(json.dumps(report, ensure_ascii=False))
        return 2

    ps = run(
        ["docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"],
        timeout=10,
    )
    if ps.returncode != 0:
        report = blocked_report("docker-ps-failed")
        atomic_json(STATUS_FILE, report)
        print(json.dumps(report, ensure_ascii=False))
        return 3

    containers = parse_ps(ps.stdout)
    old = previous.get("containers")
    prev = {
        str(row.get("id")): row
        for row in old
        if isinstance(row, dict) and row.get("id")
    } if isinstance(old, list) else {}

    due = full_inspect_due(previous)
    inspect_ids: list[str] = []
    current: dict[str, dict[str, Any]] = {}
    cache_hits = 0

    for row in containers:
        cid = str(row.get("ID"))
        fp = ps_fingerprint(row)
        cached = prev.get(cid)
        current[cid] = {
            "fingerprint": fp,
            "ps_state": row.get("State"),
            "ps_health": health_token(str(row.get("Status") or "")),
        }
        if (
            not due
            and isinstance(cached, dict)
            and cached.get("fingerprint") == fp
            and isinstance(cached.get("details"), dict)
        ):
            cache_hits += 1
        else:
            inspect_ids.append(cid)

    inspected: dict[str, dict[str, Any]] = {}
    inspect_calls = 0
    if inspect_ids:
        cp = run(["docker", "inspect", *inspect_ids], timeout=30)
        inspect_calls = 1
        if cp.returncode != 0:
            report = blocked_report("docker-inspect-failed")
            atomic_json(STATUS_FILE, report)
            print(json.dumps(report, ensure_ascii=False))
            return 4
        try:
            values = json.loads(cp.stdout)
        except json.JSONDecodeError:
            return 5
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict) and item.get("Id"):
                    inspected[str(item["Id"])] = inspect_summary(item)

    output: list[dict[str, Any]] = []
    unhealthy = restarting = dead = oom = 0
    for row in containers:
        cid = str(row.get("ID"))
        cached = prev.get(cid, {})
        details = inspected.get(cid)
        reused = False
        if details is None and isinstance(cached, dict):
            candidate = cached.get("details")
            if isinstance(candidate, dict):
                details = candidate
                reused = True
        details = details or {}
        health = str(details.get("health") or current[cid]["ps_health"] or "")
        unhealthy += int(health == "unhealthy")
        restarting += int(bool(details.get("restarting")) or row.get("State") == "restarting")
        dead += int(bool(details.get("dead")) or row.get("State") == "dead")
        oom += int(bool(details.get("oom_killed")))
        output.append(
            {
                "id": cid,
                "name": row.get("Names"),
                "fingerprint": current[cid]["fingerprint"],
                "ps_state": current[cid]["ps_state"],
                "ps_health": current[cid]["ps_health"],
                "details": details,
                "cache_reused": reused,
            }
        )

    compose_rows, compose_stats = validate_compose(
        compose_files(COMPOSE_ROOT),
        previous,
    )

    health = "healthy"
    reason = "stable"
    if unhealthy or restarting or dead or oom:
        health = "degraded"
        reason = "container-health-anomaly"

    report = attach_provenance(
        {
            "schema": "aegis-docker-efficiency/v1",
            "generated_at": now_iso(),
            "health": health,
            "reason": reason,
            "docker_server_version": version.stdout.strip() or None,
            "last_full_inspect_at": now_iso()
            if due
            else previous.get("last_full_inspect_at"),
            "full_inspect_performed": due,
            "metrics": {
                "container_count": len(output),
                "inspect_container_count": len(inspect_ids),
                "inspect_calls": inspect_calls,
                "container_cache_hits": cache_hits,
                "unhealthy": unhealthy,
                "restarting": restarting,
                "dead": dead,
                "oom_killed": oom,
                "compose_files": len(compose_rows),
                "compose_cache_hits": compose_stats["cache_hits"],
                "compose_validated": compose_stats["validated"],
            },
            "containers": output,
            "compose": compose_rows,
            "guardrails": {
                "read_only": True,
                "docker_pull": False,
                "docker_prune": False,
                "docker_restart": False,
                "registry_queries": False,
                "host_tuning": False,
                "volume_mutation": False,
            },
        },
        kind="docker-efficiency",
    )
    atomic_json(STATUS_FILE, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if health == "healthy" else 2


if __name__ == "__main__":
    raise SystemExit(main())
