#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DEFAULT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aegis_provenance import attach_provenance, verify_provenance


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_step(
    cmd: list[str],
    cwd: Path,
    timeout: int = 600,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        return {
            "cmd": cmd,
            "rc": proc.returncode,
            "stdout_tail": proc.stdout[-4000:],
            "stderr_tail": proc.stderr[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "cmd": cmd,
            "rc": 124,
            "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": ((exc.stderr or "") if isinstance(exc.stderr, str) else "")[-4000:],
            "error": "timeout",
        }
    except OSError as exc:
        return {
            "cmd": cmd,
            "rc": 126,
            "stdout_tail": "",
            "stderr_tail": str(exc),
            "error": type(exc).__name__,
        }


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def select_database_sources(report: dict[str, Any]) -> dict[str, str | None]:
    inventory = report.get("inventory")
    dbs = inventory.get("sqlite_databases", []) if isinstance(inventory, dict) else []
    if not isinstance(dbs, list):
        dbs = []

    def newest(kind: str) -> str | None:
        eligible = []
        for db in dbs:
            if not isinstance(db, dict) or kind not in db:
                continue
            path = db.get("path")
            if not isinstance(path, str) or not path:
                continue
            eligible.append((float(db.get("mtime") or 0.0), path))
        eligible.sort(reverse=True)
        return eligible[0][1] if eligible else None

    return {
        "trace_db": newest("trace_metrics"),
        "telemetry_db": newest("telemetry_metrics"),
    }


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the complete local AEGIS NAS evidence/learning/guardian cycle."
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_DEFAULT)
    parser.add_argument("--repair", action="store_true")
    parser.add_argument(
        "--push",
        action="store_true",
        help="Explicitly allow the existing guarded NAS runner to commit/push generated artifacts.",
    )
    args = parser.parse_args()

    repo = args.repo_root.resolve()
    state_dir = Path(
        os.environ.get(
            "AEGIS_REAL_CYCLE_STATE_DIR",
            Path.home() / ".local/state/aegis-real-cycle",
        )
    )
    ai_state = Path(
        os.environ.get(
            "AEGIS_AI_MINER_STATE_DIR",
            Path.home() / ".local/state/aegis-ai-miner",
        )
    )
    summary_file = state_dir / "latest.json"
    matrix_file = ai_state / "model-agent-matrix.json"
    ai_report = ai_state / "latest.json"

    result: dict[str, Any] = {
        "schema": "aegis-real-cycle/v1",
        "generated_at": now_iso(),
        "repo": str(repo),
        "mode": "local",
        "push_requested": bool(args.push),
        "repair_requested": bool(args.repair),
        "steps": {},
        "sources": {},
        "matrix": {"status": "not-run"},
        "guardian": {},
    }

    bootstrap = run_step(
        ["bash", "scripts/aegis_nas_bootstrap.sh", "--repo-root", str(repo)],
        repo,
    )
    result["steps"]["bootstrap"] = bootstrap
    if bootstrap["rc"] != 0:
        result["status"] = "blocked"
        result["reason"] = "bootstrap-failed"
        result = attach_provenance(result, kind="real-cycle")
        atomic_json(summary_file, result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 20

    nas_cmd = ["bash", "scripts/aegis_nas_run_once.sh", "--repo-root", str(repo)]
    if args.push:
        nas_cmd.append("--push")
    nas_run = run_step(nas_cmd, repo)
    result["steps"]["nas_run"] = nas_run
    if nas_run["rc"] != 0:
        result["status"] = "blocked"
        result["reason"] = "nas-run-failed"
        result = attach_provenance(result, kind="real-cycle")
        atomic_json(summary_file, result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return nas_run["rc"] or 21

    miner = run_step([sys.executable, "scripts/aegis_local_ai_miner.py"], repo)
    result["steps"]["local_ai_miner"] = miner
    if miner["rc"] != 0:
        result["status"] = "blocked"
        result["reason"] = "local-ai-miner-failed"
        result = attach_provenance(result, kind="real-cycle")
        atomic_json(summary_file, result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return miner["rc"] or 22

    report = load_json(ai_report)
    miner_ok, miner_reason = verify_provenance(report) if report else (False, "report-missing")
    result["miner_provenance"] = {
        "verified": miner_ok,
        "reason": miner_reason,
        "sha256": (
            report.get("_provenance", {}).get("sha256")
            if isinstance(report.get("_provenance"), dict)
            else None
        ),
    }
    if not miner_ok:
        result["status"] = "blocked"
        result["reason"] = "local-ai-miner-provenance-invalid"
        result = attach_provenance(result, kind="real-cycle")
        atomic_json(summary_file, result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 25

    sources = select_database_sources(report)
    result["sources"] = sources

    trace_db = sources.get("trace_db")
    telemetry_db = sources.get("telemetry_db")
    if trace_db and telemetry_db:
        matrix = run_step(
            [
                sys.executable,
                "scripts/aegis_model_agent_matrix.py",
                "--trace-db",
                trace_db,
                "--telemetry-db",
                telemetry_db,
                "--output",
                str(matrix_file),
            ],
            repo,
        )
        result["steps"]["model_agent_matrix"] = matrix
        result["matrix"] = {
            "status": "ready" if matrix["rc"] == 0 else "failed",
            "path": str(matrix_file),
        }
        if matrix["rc"] != 0:
            result["status"] = "blocked"
            result["reason"] = "model-matrix-failed"
            result = attach_provenance(result, kind="real-cycle")
            atomic_json(summary_file, result)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return matrix["rc"] or 23
    else:
        result["matrix"] = {
            "status": "skipped",
            "reason": "trace-or-telemetry-db-not-found",
            "trace_db_found": bool(trace_db),
            "telemetry_db_found": bool(telemetry_db),
        }

    guardian_cmd = [
        sys.executable,
        "scripts/aegis_guardian_cycle.py",
        "--repo-root",
        str(repo),
        "--observe-only",
    ]
    if args.repair:
        guardian_cmd.append("--repair")
    guardian = run_step(
        guardian_cmd,
        repo,
        extra_env={"AEGIS_GUARDIAN_REUSE_PREFLIGHT": "1"},
    )
    result["steps"]["guardian"] = guardian

    guardian_status = load_json(
        Path(
            os.environ.get(
                "AEGIS_GUARDIAN_STATE_DIR",
                Path.home() / ".local/state/aegis-guardian",
            )
        )
        / "status.json"
    )
    result["guardian"] = guardian_status
    result["status"] = "healthy" if guardian["rc"] == 0 else "blocked"
    result["reason"] = (
        "verified-real-cycle"
        if guardian["rc"] == 0
        else str(guardian_status.get("reason") or "guardian-not-healthy")
    )

    result = attach_provenance(result, kind="real-cycle")
    atomic_json(summary_file, result)

    status_step = run_step(
        [
            sys.executable,
            "scripts/aegis_real_status.py",
            "--repo-root",
            str(repo),
        ],
        repo,
    )
    result["steps"]["real_status"] = status_step
    if status_step["rc"] not in (0, 2):
        result["status"] = "blocked"
        result["reason"] = "real-status-generation-failed"

    result = attach_provenance(
        {k: v for k, v in result.items() if k != "_provenance"},
        kind="real-cycle",
    )
    atomic_json(summary_file, result)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "healthy" else (guardian["rc"] or status_step["rc"] or 24)


if __name__ == "__main__":
    raise SystemExit(main())
