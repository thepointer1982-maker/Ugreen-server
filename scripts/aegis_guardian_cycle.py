#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_DIR = Path(os.environ.get("AEGIS_GUARDIAN_STATE_DIR", Path.home() / ".local/state/aegis-guardian"))
STATUS_FILE = STATE_DIR / "status.json"
CARDS_FILE = STATE_DIR / "learning-cards.jsonl"
INDEX_FILE = STATE_DIR / "learning-index.json"

SAFE_SERVICE = "aegis-export.service"
STALE_LOCK_SECONDS = 7200


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.is_file():
        return data
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        if key in {"failures", "next_allowed", "last_success", "last_failure", "last_rc"}:
            data[key] = value
    return data


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def run(cmd: list[str], *, cwd: Path, timeout: int = 240) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout)


def fingerprint(event: dict[str, Any]) -> str:
    stable = json.dumps(
        {
            "mode": event.get("mode"),
            "reason": event.get("reason"),
            "action": event.get("action"),
            "rc": event.get("rc"),
        },
        sort_keys=True,
    )
    return hashlib.sha256(stable.encode()).hexdigest()[:16]


def append_learning_card(event: dict[str, Any]) -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    index = read_json(INDEX_FILE)
    fp = fingerprint(event)
    previous = index.get(fp, {}) if isinstance(index.get(fp), dict) else {}
    recurrence = int(previous.get("recurrence", 0)) + 1
    card = {
        "schema": "aegis-learning-card/v1",
        "id": f"{int(time.time())}-{fp}",
        "fingerprint": fp,
        "created_at": now_iso(),
        "recurrence": recurrence,
        **event,
    }
    with CARDS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(card, ensure_ascii=False, sort_keys=True) + "\n")
    index[fp] = {"recurrence": recurrence, "last_seen": card["created_at"], "last_outcome": card.get("outcome")}
    tmp = INDEX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(INDEX_FILE)
    return card


def safe_repairs(repo: Path, scheduler_dir: Path) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    lock = scheduler_dir / "run.lock"
    if lock.is_dir():
        age = time.time() - lock.stat().st_mtime
        if age > STALE_LOCK_SECONDS:
            try:
                lock.rmdir()
                actions.append({"action": "remove-stale-empty-scheduler-lock", "ok": True})
            except OSError as exc:
                actions.append({"action": "remove-stale-empty-scheduler-lock", "ok": False, "error": str(exc)})

    if shutil_which("systemctl"):
        unit_known = run(["systemctl", "--user", "list-unit-files", SAFE_SERVICE], cwd=repo, timeout=30)
        if unit_known.returncode == 0 and SAFE_SERVICE in unit_known.stdout:
            reset = run(["systemctl", "--user", "reset-failed", SAFE_SERVICE], cwd=repo, timeout=30)
            start = run(["systemctl", "--user", "start", SAFE_SERVICE], cwd=repo, timeout=120)
            actions.append({
                "action": "restart-aegis-export-service",
                "ok": start.returncode == 0,
                "reset_rc": reset.returncode,
                "start_rc": start.returncode,
            })
    return actions


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


def classify(failures: int, preflight_rc: int, cycle_rc: int | None) -> tuple[str, str]:
    if failures >= 5:
        return "emergency", "repeated-failures"
    if failures >= 3:
        return "degraded", "persistent-failures"
    if preflight_rc != 0:
        return "blocked", "preflight-blocked"
    if cycle_rc not in (None, 0):
        return "degraded", "cycle-failed"
    return "healthy", "verified-cycle"


def execute(repo: Path, repair: bool = False) -> dict[str, Any]:
    repo = repo.resolve()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    scheduler_dir = Path(os.environ.get("AEGIS_SCHEDULER_STATE_DIR", Path.home() / ".local/state/aegis-scheduler"))
    before = read_env(scheduler_dir / "state.env")
    before_failures = int(before.get("failures", "0") or 0)

    preflight = run(["bash", "scripts/aegis_nas_bootstrap.sh", "--repo-root", str(repo)], cwd=repo)
    repairs: list[dict[str, Any]] = []
    if repair and (preflight.returncode != 0 or before_failures >= 3):
        repairs = safe_repairs(repo, scheduler_dir)
        preflight = run(["bash", "scripts/aegis_nas_bootstrap.sh", "--repo-root", str(repo)], cwd=repo)

    cycle_rc: int | None = None
    cycle_tail = ""
    if preflight.returncode == 0:
        cycle = run(["bash", "scripts/aegis_scheduled_run.sh"], cwd=repo)
        cycle_rc = cycle.returncode
        cycle_tail = (cycle.stdout + "\n" + cycle.stderr)[-4000:]

    after = read_env(scheduler_dir / "state.env")
    failures = int(after.get("failures", str(before_failures)) or 0)
    mode, reason = classify(failures, preflight.returncode, cycle_rc)

    latest = read_json(repo / "scores/latest.json")
    autocheck = read_json(repo / "dashboard/autocheck.json")
    status = {
        "schema": "aegis-guardian-status/v1",
        "generated_at": now_iso(),
        "repo": str(repo),
        "mode": mode,
        "reason": reason,
        "repair_requested": repair,
        "repairs": repairs,
        "scheduler": {
            "before_failures": before_failures,
            "failures": failures,
            "next_allowed": after.get("next_allowed"),
            "last_rc": after.get("last_rc"),
        },
        "preflight": {
            "rc": preflight.returncode,
            "tail": (preflight.stdout + "\n" + preflight.stderr)[-4000:],
        },
        "cycle": {"rc": cycle_rc, "tail": cycle_tail},
        "evidence": {
            "network_score": latest.get("network_score"),
            "device_count": len(latest.get("devices", [])) if isinstance(latest.get("devices"), list) else None,
            "evidence_verified": autocheck.get("evidence_verified"),
            "deepdiag_score": autocheck.get("deepdiag_score"),
            "priority_findings": len(autocheck.get("priority_findings", [])) if isinstance(autocheck.get("priority_findings"), list) else None,
        },
        "guardrails": {
            "partition_changes": False,
            "bootloader_changes": False,
            "efi_changes": False,
            "formatting": False,
            "internet_exposure": False,
            "allowed_repairs": ["remove-stale-empty-scheduler-lock", "restart-aegis-export-service"],
        },
    }

    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(STATUS_FILE)

    previous = read_json(STATE_DIR / "previous-status.json")
    transition = previous.get("mode") != mode or previous.get("reason") != reason or bool(repairs)
    if transition:
        outcome = "recovered" if mode == "healthy" and previous.get("mode") in {"blocked", "degraded", "emergency"} else mode
        append_learning_card({
            "mode": mode,
            "reason": reason,
            "action": ",".join(a.get("action", "") for a in repairs) or "observe",
            "outcome": outcome,
            "rc": cycle_rc if cycle_rc is not None else preflight.returncode,
            "evidence": status["evidence"],
        })
    (STATE_DIR / "previous-status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--repair", action="store_true", help="Attempt only allowlisted reversible repairs.")
    args = parser.parse_args()
    status = execute(Path(args.repo_root), repair=args.repair)
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0 if status["mode"] == "healthy" else 2


if __name__ == "__main__":
    raise SystemExit(main())
