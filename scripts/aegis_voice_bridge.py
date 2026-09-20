#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aegis_autonomy_supervisor import queue_task
from aegis_project_context import context_packet, record_handoff
from aegis_provenance import attach_provenance

STATE_DIR = Path(
    os.environ.get(
        "AEGIS_VOICE_STATE_DIR",
        Path.home() / ".local/state/aegis-voice",
    )
)
SOCKET_PATH = Path(
    os.environ.get(
        "AEGIS_VOICE_SOCKET",
        STATE_DIR / "bridge.sock",
    )
)
STATUS_FILE = STATE_DIR / "status.json"
MAX_REQUEST_BYTES = 16 * 1024
ALLOWED_SOURCES = {"alexa", "voice"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def bridge_status(state: str = "ready") -> dict[str, Any]:
    return attach_provenance(
        {
            "schema": "aegis-voice-bridge-status/v1",
            "generated_at": now_iso(),
            "health": "healthy" if state == "ready" else state,
            "transport": "unix-socket",
            "socket": str(SOCKET_PATH),
            "network_listener": False,
            "public_port": False,
            "cloud_dependency_in_core": False,
            "accepted_sources": sorted(ALLOWED_SOURCES),
        },
        kind="voice-bridge-status",
    )


def handle_request(value: dict[str, Any]) -> dict[str, Any]:
    op = str(value.get("op") or "")
    source = str(value.get("source") or "alexa")
    if source not in ALLOWED_SOURCES:
        return {"status": "blocked", "reason": "unsupported-source"}

    if op == "context":
        return {
            "status": "ok",
            "context": context_packet(source),
        }

    if op == "enqueue":
        text = str(value.get("text") or "").strip()
        priority = str(value.get("priority") or "normal")
        if not text:
            return {"status": "blocked", "reason": "empty-text"}
        queued = queue_task(
            text,
            source=source,
            priority=priority,
        )
        return {
            "status": queued.get("status"),
            "queue": queued,
            "context": context_packet(source),
        }

    if op == "handoff":
        summary = str(value.get("summary") or "").strip()
        pending = str(value.get("pending_action") or "").strip()
        to_channel = str(value.get("to_channel") or "mcp")
        if not summary or not pending:
            return {
                "status": "blocked",
                "reason": "handoff-fields-required",
            }
        return record_handoff(
            from_channel=source,
            to_channel=to_channel,
            summary=summary,
            pending_action=pending,
            actor="voice-bridge",
        )

    return {"status": "blocked", "reason": "unsupported-operation"}


def serve() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    try:
        SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(SOCKET_PATH))
        os.chmod(SOCKET_PATH, 0o600)
        server.listen(8)
        atomic_json(STATUS_FILE, bridge_status())

        while True:
            conn, _ = server.accept()
            with conn:
                raw = b""
                while len(raw) <= MAX_REQUEST_BYTES:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    raw += chunk
                    if b"\n" in raw:
                        raw = raw.split(b"\n", 1)[0]
                        break
                if len(raw) > MAX_REQUEST_BYTES:
                    response = {
                        "status": "blocked",
                        "reason": "request-too-large",
                    }
                else:
                    try:
                        value = json.loads(raw.decode("utf-8"))
                        if not isinstance(value, dict):
                            raise ValueError("request must be object")
                        response = handle_request(value)
                    except Exception as exc:
                        response = {
                            "status": "blocked",
                            "reason": f"invalid-request:{type(exc).__name__}",
                        }
                conn.sendall(
                    (
                        json.dumps(response, ensure_ascii=False)
                        + "\n"
                    ).encode("utf-8")
                )


def request(payload: dict[str, Any]) -> dict[str, Any]:
    raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    if len(raw) > MAX_REQUEST_BYTES:
        return {"status": "blocked", "reason": "request-too-large"}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(str(SOCKET_PATH))
        client.sendall(raw)
        chunks = []
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
    return json.loads(b"".join(chunks).decode("utf-8").splitlines()[0])


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    req = sub.add_parser("request")
    req.add_argument("--json", required=True)
    sub.add_parser("status")
    args = parser.parse_args()

    if args.cmd == "serve":
        return serve()
    if args.cmd == "status":
        if STATUS_FILE.is_file():
            print(STATUS_FILE.read_text(encoding="utf-8"))
            return 0
        print(json.dumps({"status": "not-yet-running"}))
        return 2

    payload = json.loads(args.json)
    result = request(payload)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") in {"ok", "queued", "recorded"} else 5


if __name__ == "__main__":
    raise SystemExit(main())
