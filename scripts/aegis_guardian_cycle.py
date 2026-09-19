#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aegis_provenance import verify_provenance
from aegis_last_known_good import active_status, observe_active_health, provisional_status, observe_provisional

STATE_DIR = Path(os.environ.get("AEGIS_GUARDIAN_STATE_DIR", Path.home() / ".local/state/aegis-guardian"))
STATUS_FILE = STATE_DIR / "status.json"
CARDS_FILE = STATE_DIR / "learning-cards.jsonl"
INDEX_FILE = STATE_DIR / "learning-index.json"
LEARNING_LOCK = STATE_DIR / "learning.lock"
AI_MINER_REPORT = Path(os.environ.get(
    "AEGIS_AI_MINER_REPORT",
    Path.home() / ".local/state/aegis-ai-miner/latest.json",
))

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
    try:
        return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return subprocess.CompletedProcess(
            cmd,
            124,
            stdout,
            (stderr + "\nAEGIS_GUARDIAN timeout").strip(),
        )
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 126, "", f"AEGIS_GUARDIAN exec_error: {exc}")


def parse_nonnegative_int(value: str | None, default: int = 0) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


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
    with LEARNING_LOCK.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            index = read_json(INDEX_FILE)
            fp = fingerprint(event)
            previous = index.get(fp, {}) if isinstance(index.get(fp), dict) else {}
            recurrence = parse_nonnegative_int(str(previous.get("recurrence", 0)), 0) + 1
            card = {
                "schema": "aegis-learning-card/v1",
                "id": f"{time.time_ns()}-{fp}",
                "fingerprint": fp,
                "created_at": now_iso(),
                "recurrence": recurrence,
                **event,
            }
            with CARDS_FILE.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(card, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            index[fp] = {
                "recurrence": recurrence,
                "last_seen": card["created_at"],
                "last_outcome": card.get("outcome"),
            }
            tmp = INDEX_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(index, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            tmp.replace(INDEX_FILE)
            return card
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def safe_repairs(
    repo: Path,
    scheduler_dir: Path,
    *,
    allow_service_restart: bool,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []

    # Scheduler lock recovery belongs exclusively to aegis_scheduled_run.sh.
    # The guardian must not independently delete scheduler locks because the
    # scheduler validates PID + boot-id ownership before recovery.
    if allow_service_restart and shutil_which("systemctl"):
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


def probation_passes(status: dict[str, Any]) -> bool:
    if status.get("mode") != "healthy":
        return False
    evidence = status.get("evidence")
    if not isinstance(evidence, dict):
        return False
    if evidence.get("evidence_verified") is False:
        return False
    if evidence.get("local_ai_provenance_verified") is False:
        return False
    return True


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
    before_failures = parse_nonnegative_int(before.get("failures"), 0)

    preflight = run(["bash", "scripts/aegis_nas_bootstrap.sh", "--repo-root", str(repo)], cwd=repo)
    repairs: list[dict[str, Any]] = []
    if repair:
        repairs = safe_repairs(
            repo,
            scheduler_dir,
            allow_service_restart=before_failures >= 3,
        )
        if repairs:
            preflight = run(["bash", "scripts/aegis_nas_bootstrap.sh", "--repo-root", str(repo)], cwd=repo)

    cycle_rc: int | None = None
    cycle_tail = ""
    if preflight.returncode == 0:
        cycle = run(["bash", "scripts/aegis_scheduled_run.sh"], cwd=repo)
        cycle_rc = cycle.returncode
        cycle_tail = (cycle.stdout + "\n" + cycle.stderr)[-4000:]

    after = read_env(scheduler_dir / "state.env")
    failures = parse_nonnegative_int(after.get("failures"), before_failures)
    mode, reason = classify(failures, preflight.returncode, cycle_rc)

    latest = read_json(repo / "scores/latest.json")
    autocheck = read_json(repo / "dashboard/autocheck.json")
    ai_report = read_json(AI_MINER_REPORT)
    ai_provenance_ok = False
    ai_provenance_reason = "not-present"
    if ai_report:
        ai_provenance_ok, ai_provenance_reason = verify_provenance(ai_report)
    ai_findings = ai_report.get("findings", []) if isinstance(ai_report.get("findings"), list) else []
    ai_codes = [f.get("code") for f in ai_findings if isinstance(f, dict) and f.get("code")]
    if ai_report and not ai_provenance_ok:
        mode = "blocked"
        reason = "local-ai-provenance-invalid"
        ai_codes.append("LOCAL_AI_PROVENANCE_INVALID")
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
            "local_ai_findings": ai_codes,
            "local_ai_provenance_verified": ai_provenance_ok if ai_report else None,
            "local_ai_provenance_reason": ai_provenance_reason if ai_report else None,
            "local_ai_sha256": (
                ai_report.get("_provenance", {}).get("sha256")
                if isinstance(ai_report.get("_provenance"), dict)
                else None
            ),
            "ollama_reachable": ai_report.get("ollama", {}).get("reachable") if isinstance(ai_report.get("ollama"), dict) else None,
            "ollama_model_count": len(ai_report.get("ollama", {}).get("models", [])) if isinstance(ai_report.get("ollama"), dict) and isinstance(ai_report.get("ollama", {}).get("models"), list) else None,
            "local_ai_db_count": len(ai_report.get("inventory", {}).get("sqlite_databases", [])) if isinstance(ai_report.get("inventory"), dict) and isinstance(ai_report.get("inventory", {}).get("sqlite_databases"), list) else None,
        },
        "guardrails": {
            "partition_changes": False,
            "bootloader_changes": False,
            "efi_changes": False,
            "formatting": False,
            "internet_exposure": False,
            "allowed_repairs": ["restart-aegis-export-service"],
        },
    }

    provisional = provisional_status()
    if provisional.get("status") == "ok":
        raw_target = os.environ.get("AEGIS_LKG_ROLLBACK_TARGET")
        rollback_target = Path(raw_target).expanduser() if raw_target else None
        status["probation"] = observe_provisional(
            passed=probation_passes(status),
            evidence={
                "guardian_mode": status.get("mode"),
                "guardian_reason": status.get("reason"),
                "network_score": status["evidence"].get("network_score"),
                "evidence_verified": status["evidence"].get("evidence_verified"),
                "local_ai_provenance_verified": status["evidence"].get("local_ai_provenance_verified"),
            },
            rollback_destination=rollback_target,
        )
        if status["probation"].get("status") == "rejected":
            status["mode"] = "blocked"
            status["reason"] = "provisional-regression-rollback"
    else:
        status["probation"] = {"status": "none"}

    active = active_status()
    if active.get("status") == "ok":
        active_state = active.get("active", {}).get("state")
        if active_state == "validating":
            status["activation"] = observe_active_health(
                passed=probation_passes(status),
                evidence={
                    "guardian_mode": status.get("mode"),
                    "guardian_reason": status.get("reason"),
                    "evidence_verified": status["evidence"].get("evidence_verified"),
                    "local_ai_provenance_verified": status["evidence"].get("local_ai_provenance_verified"),
                },
            )
            if status["activation"].get("status") == "regression":
                status["mode"] = "blocked"
                status["reason"] = "active-regression-rollback"
        else:
            status["activation"] = {"status": active_state or "unknown"}
    else:
        status["activation"] = {"status": "none"}

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
