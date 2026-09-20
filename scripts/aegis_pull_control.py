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

CONTROL_REF = "refs/remotes/origin/aegis-control"
DEV_REF = "refs/remotes/origin/aegis/guardian-learning-mcp"
CONTROL_PATH = ".aegis-control/request.json"
ALLOWED = {"status", "real-cycle", "deploy-retry"}

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def run(cmd: list[str], cwd: Path, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed rc={p.returncode}: {cmd!r}\n{p.stderr[-2000:]}")
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
        return data if isinstance(data, dict) else {"last_sequence": 0}
    except Exception:
        return {"last_sequence": 0}

def normalize_origin(url: str) -> str:
    return url.strip().removesuffix(".git").replace("git@github.com:", "https://github.com/")

def validate_origin(repo: Path) -> None:
    url = run(["git", "remote", "get-url", "origin"], repo).stdout.strip()
    expected = "https://github.com/thepointer1982-maker/Ugreen-server"
    if normalize_origin(url) != expected:
        raise RuntimeError(f"blocked unexpected origin: {url}")

def fetch_refs(repo: Path) -> None:
    run([
        "git", "fetch", "--prune", "origin",
        "+refs/heads/aegis-control:refs/remotes/origin/aegis-control",
        "+refs/heads/aegis/guardian-learning-mcp:refs/remotes/origin/aegis/guardian-learning-mcp",
    ], repo, timeout=180)

def load_request(repo: Path) -> dict:
    raw = run(["git", "show", f"{CONTROL_REF}:{CONTROL_PATH}"], repo).stdout
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("blocked request is not an object")
    return data

def validate_request(req: dict, last_sequence: int) -> tuple[str, int, str]:
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
    p = run(["git", "merge-base", "--is-ancestor", sha, DEV_REF], repo, check=False)
    if p.returncode != 0:
        raise RuntimeError("blocked trusted_sha is not on tested development branch")

def prepare_runtime(repo: Path, runtime: Path, sha: str) -> None:
    if not (runtime / ".git").exists():
        runtime.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--no-checkout", str(repo), str(runtime)], repo.parent, timeout=180)
    else:
        run(["git", "remote", "set-url", "origin", str(repo)], runtime)
    run(["git", "fetch", "--prune", "origin"], runtime, timeout=180)
    run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], runtime)
    run(["git", "checkout", "--detach", "--force", sha], runtime)
    run(["git", "reset", "--hard", sha], runtime)
    run(["git", "clean", "-fdx"], runtime)
    dirty = run(["git", "status", "--porcelain"], runtime).stdout.strip()
    if dirty:
        raise RuntimeError("blocked runtime checkout is dirty")

def execute(action: str, runtime: Path) -> subprocess.CompletedProcess[str]:
    if action == "status":
        cmd = ["python3", "scripts/aegis_real_status.py", "--repo-root", str(runtime)]
        timeout = 180
    elif action == "real-cycle":
        cmd = ["python3", "scripts/aegis_real_cycle.py", "--repo-root", str(runtime)]
        timeout = 900
    else:
        cmd = ["bash", "scripts/aegis_remote_apply_retry.sh", "--repo-root", str(runtime), "--repair"]
        timeout = 1800
    return run(cmd, runtime, timeout=timeout, check=False)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--state-file", default=str(Path.home() / ".local/state/aegis-pull-control/state.json"))
    ap.add_argument("--runtime-root", default=str(Path.home() / ".local/share/aegis-pull-control/runtime"))
    args = ap.parse_args()

    repo = Path(args.repo_root).resolve()
    state_path = Path(args.state_file).expanduser().resolve()
    runtime = Path(args.runtime_root).expanduser().resolve() / "Ugreen-server"
    state = read_state(state_path)
    last_sequence = int(state.get("last_sequence") or 0)

    try:
        validate_origin(repo)
        fetch_refs(repo)
        req = load_request(repo)
        action, sequence, trusted = validate_request(req, last_sequence)
        if trusted == "stale":
            state.update({"status": "idle", "last_checked_at": now_iso(), "seen_sequence": sequence})
            atomic_write_json(state_path, state)
            print(json.dumps(state, ensure_ascii=False))
            return 0
        validate_trusted_sha(repo, trusted)
        prepare_runtime(repo, runtime, trusted)
        p = execute(action, runtime)
        result = {
            "status": "success" if p.returncode == 0 else "failed",
            "action": action,
            "sequence": sequence,
            "trusted_sha": trusted,
            "returncode": p.returncode,
            "completed_at": now_iso(),
            "stdout_tail": p.stdout[-6000:],
            "stderr_tail": p.stderr[-6000:],
        }
        if p.returncode == 0:
            result["last_sequence"] = sequence
        else:
            result["last_sequence"] = last_sequence
        atomic_write_json(state_path, result)
        print(json.dumps(result, ensure_ascii=False))
        return p.returncode
    except Exception as exc:
        result = {
            "status": "blocked",
            "last_sequence": last_sequence,
            "checked_at": now_iso(),
            "error": str(exc),
        }
        atomic_write_json(state_path, result)
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 20

if __name__ == "__main__":
    raise SystemExit(main())
