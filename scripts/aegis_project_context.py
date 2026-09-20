#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(SCRIPT_DIR))

from aegis_provenance import attach_provenance, verify_provenance

REPO_ROOT = Path(
    os.environ.get("AEGIS_REPO_ROOT", Path(__file__).resolve().parents[1])
).resolve()
PROJECT_FILE = Path(
    os.environ.get(
        "AEGIS_PROJECT_DEFINITION",
        REPO_ROOT / "config" / "project" / "aegis-core.json",
    )
)
AUTONOMY_POLICY_FILE = Path(
    os.environ.get(
        "AEGIS_AUTONOMY_POLICY",
        REPO_ROOT / "config" / "autonomy" / "aegis-max-local.json",
    )
)
STATE_DIR = Path(
    os.environ.get(
        "AEGIS_PROJECT_STATE_DIR",
        Path.home() / ".local" / "state" / "aegis-project",
    )
)
STATE_FILE = STATE_DIR / "context.json"
HANDOFFS_FILE = STATE_DIR / "handoffs.jsonl"

MAX_SUMMARY = 6000
MAX_PENDING = 1200
MAX_ITEM = 1200
MAX_ITEMS = 30
ALLOWED_CHANNELS = {"chat", "alexa", "voice", "iphone", "codex-local", "opencode-local", "mcp"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def style_fingerprint(project: dict[str, Any]) -> str:
    style = project.get("style_contract")
    if not isinstance(style, dict):
        style = {}
    return hashlib.sha256(_canonical(style)).hexdigest()


def _git_head() -> str | None:
    try:
        cp = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        value = cp.stdout.strip()
        return value if cp.returncode == 0 and len(value) == 40 else None
    except Exception:
        return None


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _clean_text(value: str, limit: int, field: str) -> str:
    value = str(value).strip()
    if len(value) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    return value


def _clean_items(values: list[str] | None, field: str) -> list[str]:
    if not values:
        return []
    if len(values) > MAX_ITEMS:
        raise ValueError(f"{field} exceeds {MAX_ITEMS} items")
    return [_clean_text(v, MAX_ITEM, field) for v in values]


def load_project() -> dict[str, Any]:
    return _load_json(PROJECT_FILE)


def load_autonomy_policy() -> dict[str, Any]:
    try:
        value = _load_json(AUTONOMY_POLICY_FILE)
    except Exception:
        return {}
    if value.get("schema") != "aegis-autonomy-policy/v1":
        return {}
    return value


def autonomy_packet() -> dict[str, Any]:
    policy = load_autonomy_policy()
    if not policy:
        return {"status": "missing"}
    return {
        "status": "active",
        "profile": policy.get("profile"),
        "revision": policy.get("revision"),
        "local_first": policy.get("local_first"),
        "recurring_cloud_ai_cost_allowed": policy.get(
            "recurring_cloud_ai_cost_allowed"
        ),
        "automatic_capabilities": policy.get("automatic_capabilities", {}),
        "hard_blocks": policy.get("hard_blocks", []),
        "docker_autorepair": policy.get("docker_autorepair", {}),
    }


def initial_state() -> dict[str, Any]:
    project = load_project()
    project_id = str(project.get("project_id") or "aegis-core")
    state = {
        "schema": "aegis-project-context/v1",
        "project_id": project_id,
        "thread_id": f"{project_id}-main",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "turn_sequence": 0,
        "source_channel": "mcp",
        "last_handoff_channel": None,
        "summary": (
            "AEGIS core development is active. The Windows/Lenovo incident is separated. "
            "The current program gate is physical UGREEN validation of local Codex OSS, "
            "Ollama and MCP-backed project continuity."
        ),
        "pending_action": str(project.get("default_next_action") or ""),
        "decisions": [
            "Use the clean pre-Lenovo continuation branch as the AEGIS core line.",
            "Keep the production coding path local-first and zero recurring AI/API cost.",
            "Persist project continuity on the UGREEN NAS, not inside one model session.",
        ],
        "blockers": [
            "Physical UGREEN gate has not yet produced measured runtime evidence."
        ],
        "style_fingerprint": style_fingerprint(project),
        "style_contract": project.get("style_contract", {}),
        "hard_constraints": project.get("hard_constraints", []),
        "program_gate": project.get("current_program_gate"),
        "autonomy": autonomy_packet(),
        "repo_head": _git_head(),
    }
    return attach_provenance(state, kind="project-context")


def ensure_state() -> dict[str, Any]:
    if STATE_FILE.is_file():
        try:
            state = _load_json(STATE_FILE)
            ok, reason = verify_provenance(state)
            if ok:
                return state
            raise ValueError(reason)
        except Exception:
            pass
    state = initial_state()
    _atomic_json(STATE_FILE, state)
    return state


def read_state() -> dict[str, Any]:
    state = ensure_state()
    ok, reason = verify_provenance(state)
    if not ok:
        return {"status": "blocked", "reason": reason}
    return state


def record_handoff(
    *,
    from_channel: str,
    to_channel: str,
    summary: str,
    pending_action: str,
    decisions: list[str] | None = None,
    blockers: list[str] | None = None,
    actor: str = "unknown",
) -> dict[str, Any]:
    if from_channel not in ALLOWED_CHANNELS or to_channel not in ALLOWED_CHANNELS:
        return {"status": "blocked", "reason": "unsupported-channel"}
    current = read_state()
    if current.get("status") == "blocked":
        return current

    project = load_project()
    expected_style = style_fingerprint(project)
    if current.get("style_fingerprint") != expected_style:
        return {"status": "blocked", "reason": "style-contract-drift"}

    next_state = {
        "schema": "aegis-project-context/v1",
        "project_id": current["project_id"],
        "thread_id": current["thread_id"],
        "created_at": current["created_at"],
        "updated_at": now_iso(),
        "turn_sequence": int(current.get("turn_sequence", 0)) + 1,
        "source_channel": to_channel,
        "last_handoff_channel": from_channel,
        "summary": _clean_text(summary, MAX_SUMMARY, "summary"),
        "pending_action": _clean_text(pending_action, MAX_PENDING, "pending_action"),
        "decisions": (
            _clean_items(decisions, "decisions")
            if decisions is not None
            else list(current.get("decisions", []))
        ),
        "blockers": (
            _clean_items(blockers, "blockers")
            if blockers is not None
            else list(current.get("blockers", []))
        ),
        "style_fingerprint": expected_style,
        "style_contract": project.get("style_contract", {}),
        "hard_constraints": project.get("hard_constraints", []),
        "program_gate": project.get("current_program_gate"),
        "autonomy": autonomy_packet(),
        "repo_head": _git_head(),
    }
    signed = attach_provenance(
        next_state,
        kind="project-context",
        parent_sha256=current["_provenance"]["sha256"],
        parent_kind="project-context",
    )
    _atomic_json(STATE_FILE, signed)

    handoff = attach_provenance(
        {
            "schema": "aegis-project-handoff/v1",
            "handoff_id": str(uuid.uuid4()),
            "recorded_at": now_iso(),
            "actor": _clean_text(actor, 200, "actor"),
            "from_channel": from_channel,
            "to_channel": to_channel,
            "project_id": signed["project_id"],
            "thread_id": signed["thread_id"],
            "turn_sequence": signed["turn_sequence"],
            "style_fingerprint": signed["style_fingerprint"],
            "pending_action": signed["pending_action"],
            "context_sha256": signed["_provenance"]["sha256"],
        },
        kind="project-handoff",
        parent_sha256=signed["_provenance"]["sha256"],
        parent_kind="project-context",
    )
    HANDOFFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with HANDOFFS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(handoff, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())

    return {
        "status": "recorded",
        "context": signed,
        "handoff": handoff,
    }


def continuity_check(
    *,
    project_id: str,
    thread_id: str,
    style_hash: str,
    pending_action: str | None = None,
) -> dict[str, Any]:
    current = read_state()
    if current.get("status") == "blocked":
        return current

    checks = {
        "project_id": project_id == current.get("project_id"),
        "thread_id": thread_id == current.get("thread_id"),
        "style_fingerprint": style_hash == current.get("style_fingerprint"),
    }
    if pending_action is not None:
        checks["pending_action"] = pending_action == current.get("pending_action")
    passed = all(checks.values())
    return {
        "status": "pass" if passed else "blocked",
        "checks": checks,
        "project_id": current.get("project_id"),
        "thread_id": current.get("thread_id"),
        "style_fingerprint": current.get("style_fingerprint"),
        "pending_action": current.get("pending_action"),
    }


def context_packet(channel: str) -> dict[str, Any]:
    if channel not in ALLOWED_CHANNELS:
        return {"status": "blocked", "reason": "unsupported-channel"}
    state = read_state()
    if state.get("status") == "blocked":
        return state
    voice = channel in {"alexa", "voice"}
    return {
        "schema": "aegis-context-packet/v1",
        "channel": channel,
        "project_id": state["project_id"],
        "thread_id": state["thread_id"],
        "summary": state["summary"],
        "pending_action": state["pending_action"],
        "decisions": state.get("decisions", []),
        "blockers": state.get("blockers", []),
        "program_gate": state.get("program_gate"),
        "hard_constraints": state.get("hard_constraints", []),
        "autonomy": autonomy_packet(),
        "style_fingerprint": state["style_fingerprint"],
        "style_contract": state.get("style_contract", {}),
        "render_policy": {
            "voice_concise": voice,
            "preserve_project_names": True,
            "preserve_pending_action": True,
            "no_style_reset": True,
            "invent_missing_context": False,
        },
        "context_sha256": state["_provenance"]["sha256"],
    }


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    packet = sub.add_parser("packet")
    packet.add_argument("--channel", required=True)
    args = p.parse_args()

    if args.cmd == "show":
        print(json.dumps(read_state(), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "packet":
        print(json.dumps(context_packet(args.channel), indent=2, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
