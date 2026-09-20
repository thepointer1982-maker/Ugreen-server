#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aegis_provenance import attach_provenance, verify_provenance

CONTROL_HEAD = "refs/heads/aegis-control"
CONTROL_REF = "refs/remotes/origin/aegis-control"
DEV_HEAD = "refs/heads/aegis/resume-pre-lenovo-20260920"
DEV_REF = "refs/remotes/origin/aegis/resume-pre-lenovo-20260920"
CONTROL_PATH = ".aegis-control/request.json"
ALLOWED = {"status", "real-cycle", "deploy-retry"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(
    cmd: list[str],
    cwd: Path,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if check and p.returncode != 0:
        raise RuntimeError(
            f"command failed rc={p.returncode}: {cmd!r}\n{p.stderr[-2000:]}"
        )
    return p


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(raw, path)
    finally:
        try:
            os.unlink(raw)
        except FileNotFoundError:
            pass


def read_state(path: Path) -> dict:
    if not path.is_file():
        return {"last_sequence": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"last_sequence": 0}
    if not isinstance(data, dict):
        return {"last_sequence": 0}
    ok, _ = verify_provenance(data)
    if not ok:
        return {"last_sequence": 0}
    return data


def write_state(path: Path, data: dict, previous: dict | None = None) -> dict:
    parent_sha = None
    if isinstance(previous, dict):
        ok, _ = verify_provenance(previous)
        if ok and isinstance(previous.get("_provenance"), dict):
            parent_sha = previous["_provenance"].get("sha256")
    signed = attach_provenance(
        data,
        kind="pull-control-state",
        parent_sha256=parent_sha,
        parent_kind="pull-control-state" if parent_sha else None,
    )
    atomic_write_json(path, signed)
    return signed


def normalize_origin(url: str) -> str:
    return (
        url.strip()
        .removesuffix(".git")
        .replace("git@github.com:", "https://github.com/")
    )


def validate_origin(repo: Path) -> None:
    url = run(["git", "remote", "get-url", "origin"], repo).stdout.strip()
    expected = "https://github.com/thepointer1982-maker/Ugreen-server"
    if normalize_origin(url) != expected:
        raise RuntimeError(f"blocked unexpected origin: {url}")


def remote_control_head(repo: Path) -> str:
    p = run(
        ["git", "ls-remote", "--heads", "origin", CONTROL_HEAD],
        repo,
        timeout=30,
    )
    lines = [line.strip() for line in p.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError("blocked unable to resolve unique control head")
    sha = lines[0].split()[0]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("blocked invalid remote control head")
    return sha


def can_fast_idle(state: dict, control_head: str, last_sequence: int) -> bool:
    try:
        seen = int(state.get("seen_sequence") or state.get("sequence") or 0)
    except (TypeError, ValueError):
        seen = 0
    return (
        state.get("control_head") == control_head
        and state.get("status") in {"success", "idle"}
        and last_sequence >= seen
    )


def fetch_control_ref(repo: Path) -> None:
    run(
        [
            "git",
            "fetch",
            "--no-tags",
            "origin",
            f"+{CONTROL_HEAD}:{CONTROL_REF}",
        ],
        repo,
        timeout=90,
    )


def fetch_dev_ref(repo: Path) -> None:
    run(
        [
            "git",
            "fetch",
            "--no-tags",
            "origin",
            f"+{DEV_HEAD}:{DEV_REF}",
        ],
        repo,
        timeout=120,
    )


def load_request(repo: Path) -> dict:
    raw = run(["git", "show", f"{CONTROL_REF}:{CONTROL_PATH}"], repo).stdout
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("blocked request is not an object")
    return data


def validate_request(
    req: dict,
    last_sequence: int,
) -> tuple[str, int, str]:
    if req.get("schema") != "aegis-control/v2":
        raise RuntimeError("blocked unsupported schema")
    action = req.get("action")
    if action not in ALLOWED:
        raise RuntimeError(f"blocked action: {action!r}")
    sequence = req.get("sequence")
    if not isinstance(sequence, int) or sequence < 1:
        raise RuntimeError("blocked invalid sequence")
    if sequence <= last_sequence:
        return action, sequence, "stale"
    sha = req.get("trusted_sha")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("blocked invalid trusted_sha")
    return action, sequence, sha


def validate_trusted_sha(repo: Path, sha: str) -> None:
    run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], repo)
    p = run(
        ["git", "merge-base", "--is-ancestor", sha, DEV_REF],
        repo,
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(
            "blocked trusted_sha is not on tested development branch"
        )


def prepare_runtime(repo: Path, runtime: Path, sha: str) -> None:
    if not (runtime / ".git").exists():
        runtime.parent.mkdir(parents=True, exist_ok=True)
        run(
            ["git", "clone", "--no-checkout", str(repo), str(runtime)],
            repo.parent,
            timeout=180,
        )
    else:
        run(["git", "remote", "set-url", "origin", str(repo)], runtime)

    has_commit = run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        runtime,
        check=False,
    )
    if has_commit.returncode != 0:
        targeted = run(
            ["git", "fetch", "--no-tags", "origin", sha],
            runtime,
            timeout=120,
            check=False,
        )
        if targeted.returncode != 0:
            run(["git", "fetch", "--no-tags", "origin"], runtime, timeout=180)

    run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], runtime)
    current = run(
        ["git", "rev-parse", "HEAD"],
        runtime,
        check=False,
    )
    if current.returncode != 0 or current.stdout.strip() != sha:
        run(["git", "checkout", "--detach", "--force", sha], runtime)
    run(["git", "reset", "--hard", sha], runtime)
    run(["git", "clean", "-fdx"], runtime)
    dirty = run(["git", "status", "--porcelain"], runtime).stdout.strip()
    if dirty:
        raise RuntimeError("blocked runtime checkout is dirty")


def execute(
    action: str,
    runtime: Path,
) -> subprocess.CompletedProcess[str]:
    if action == "status":
        cmd = [
            "python3",
            "scripts/aegis_real_status.py",
            "--repo-root",
            str(runtime),
        ]
        timeout = 180
    elif action == "real-cycle":
        cmd = [
            "python3",
            "scripts/aegis_real_cycle.py",
            "--repo-root",
            str(runtime),
        ]
        timeout = 900
    else:
        cmd = [
            "bash",
            "scripts/aegis_remote_apply_retry.sh",
            "--repo-root",
            str(runtime),
            "--repair",
        ]
        timeout = 1800
    return run(cmd, runtime, timeout=timeout, check=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    ap.add_argument(
        "--state-file",
        default=str(
            Path.home() / ".local/state/aegis-pull-control/state.json"
        ),
    )
    ap.add_argument(
        "--runtime-root",
        default=str(
            Path.home() / ".local/share/aegis-pull-control/runtime"
        ),
    )
    args = ap.parse_args()

    repo = Path(args.repo_root).resolve()
    state_path = Path(args.state_file).expanduser().resolve()
    runtime = Path(args.runtime_root).expanduser().resolve() / "Ugreen-server"
    state = read_state(state_path)
    last_sequence = int(state.get("last_sequence") or 0)
    control_head = str(state.get("control_head") or "")

    try:
        validate_origin(repo)
        control_head = remote_control_head(repo)

        if can_fast_idle(state, control_head, last_sequence):
            state.update(
                {
                    "status": "idle",
                    "last_checked_at": now_iso(),
                    "control_head": control_head,
                }
            )
            state["transport"] = "outbound-pull"
            state["runner_required"] = False
            state = write_state(state_path, state, previous=read_state(state_path))
            print(json.dumps(state, ensure_ascii=False))
            return 0

        fetch_control_ref(repo)
        req = load_request(repo)
        action, sequence, trusted = validate_request(req, last_sequence)

        if trusted == "stale":
            state.update(
                {
                    "status": "idle",
                    "last_checked_at": now_iso(),
                    "seen_sequence": sequence,
                    "control_head": control_head,
                }
            )
            state["transport"] = "outbound-pull"
            state["runner_required"] = False
            state = write_state(state_path, state, previous=read_state(state_path))
            print(json.dumps(state, ensure_ascii=False))
            return 0

        fetch_dev_ref(repo)
        validate_trusted_sha(repo, trusted)
        prepare_runtime(repo, runtime, trusted)
        p = execute(action, runtime)

        result = {
            "status": "success" if p.returncode == 0 else "failed",
            "transport": "outbound-pull",
            "runner_required": False,
            "action": action,
            "sequence": sequence,
            "seen_sequence": sequence,
            "trusted_sha": trusted,
            "control_head": control_head,
            "returncode": p.returncode,
            "completed_at": now_iso(),
            "stdout_tail": p.stdout[-6000:],
            "stderr_tail": p.stderr[-6000:],
        }
        if p.returncode == 0:
            result["last_sequence"] = sequence
        else:
            result["last_sequence"] = last_sequence

        result = write_state(state_path, result, previous=state)
        print(json.dumps(result, ensure_ascii=False))
        return p.returncode
    except Exception as exc:
        result = {
            "status": "blocked",
            "transport": "outbound-pull",
            "runner_required": False,
            "last_sequence": last_sequence,
            "control_head": control_head or state.get("control_head"),
            "checked_at": now_iso(),
            "error": str(exc),
        }
        result = write_state(state_path, result, previous=state)
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 20


if __name__ == "__main__":
    raise SystemExit(main())
