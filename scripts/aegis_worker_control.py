#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aegis_provenance import attach_provenance

REPO_ROOT = Path(
    os.environ.get("AEGIS_REPO_ROOT", Path(__file__).resolve().parents[1])
).resolve()
CONFIG = Path(
    os.environ.get(
        "AEGIS_WORKER_CONFIG",
        REPO_ROOT / "config" / "workers" / "aegis-workers.json",
    )
)
STATE_DIR = Path(
    os.environ.get(
        "AEGIS_WORKER_STATE_DIR",
        Path.home() / ".local/state/aegis-workers",
    )
)
STATUS_FILE = STATE_DIR / "status.json"
MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config() -> dict[str, Any]:
    value = json.loads(CONFIG.read_text(encoding="utf-8"))
    if value.get("schema") != "aegis-worker-fleet/v1":
        raise ValueError("invalid worker config schema")
    return value


def worker_by_id(config: dict[str, Any], worker_id: str) -> dict[str, Any] | None:
    for row in config.get("workers", []):
        if isinstance(row, dict) and row.get("id") == worker_id:
            return row
    return None


def configured(row: dict[str, Any]) -> bool:
    wol = row.get("wol") if isinstance(row.get("wol"), dict) else {}
    return bool(
        row.get("enabled")
        and wol.get("enabled")
        and isinstance(wol.get("mac"), str)
        and MAC_RE.fullmatch(wol["mac"])
        and wol.get("port") == 9
    )


def fleet_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    workers = []
    for row in cfg.get("workers", []):
        if not isinstance(row, dict):
            continue
        wol = row.get("wol") if isinstance(row.get("wol"), dict) else {}
        workers.append({
            "id": row.get("id"),
            "type": row.get("type"),
            "role": row.get("role"),
            "enabled": bool(row.get("enabled")),
            "configured": configured(row),
            "wol_enabled": bool(wol.get("enabled")),
            "ssh_enabled": bool(
                isinstance(row.get("ssh"), dict)
                and row["ssh"].get("enabled")
            ),
        })
    return attach_provenance(
        {
            "schema": "aegis-worker-fleet-status/v1",
            "generated_at": now_iso(),
            "health": "healthy",
            "workers": workers,
            "policy": cfg.get("policy", {}),
        },
        kind="worker-fleet-status",
    )


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


def wake(worker_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    cfg = load_config()
    row = worker_by_id(cfg, worker_id)
    if row is None:
        return {"status": "blocked", "reason": "worker-not-allowlisted"}
    if not configured(row):
        return {"status": "blocked", "reason": "worker-not-configured"}

    wol = row["wol"]
    mac = str(wol["mac"])
    broadcast = str(wol.get("broadcast") or "255.255.255.255")
    if broadcast != "255.255.255.255" and not broadcast.startswith(
        ("10.", "172.16.", "172.17.", "172.18.", "172.19.",
         "172.2", "172.30.", "172.31.", "192.168.")
    ):
        return {"status": "blocked", "reason": "non-private-broadcast"}

    payload = bytes.fromhex("FF" * 6 + mac.replace(":", "") * 16)
    if not dry_run:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(payload, (broadcast, 9))

    return {
        "status": "sent" if not dry_run else "dry-run",
        "worker_id": worker_id,
        "transport": "wake-on-lan",
        "broadcast": broadcast,
        "port": 9,
        "payload_bytes": len(payload),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    wake_p = sub.add_parser("wake")
    wake_p.add_argument("worker_id")
    wake_p.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.cmd == "status":
        status = fleet_status()
        atomic_json(STATUS_FILE, status)
        print(json.dumps(status, ensure_ascii=False))
        return 0

    result = wake(args.worker_id, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") in {"sent", "dry-run"} else 5


if __name__ == "__main__":
    raise SystemExit(main())
