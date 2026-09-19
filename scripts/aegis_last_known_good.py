#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
import sys
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aegis_provenance import attach_provenance, load_verified_json, verify_provenance

ROOT = Path(os.environ.get(
    "AEGIS_LKG_STATE_DIR",
    Path.home() / ".local/state/aegis-last-known-good",
))
VERSIONS = ROOT / "versions"
POINTER = ROOT / "last-known-good.json"
HISTORY = ROOT / "history.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def snapshot_artifact(source: Path, *, kind: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    value, ok, reason = load_verified_json(source)
    if not ok:
        return {
            "status": "blocked",
            "reason": f"source-provenance-{reason}",
            "source": str(source),
        }

    digest = value["_provenance"]["sha256"]
    target_dir = VERSIONS / kind / digest
    target = target_dir / source.name
    target_dir.mkdir(parents=True, exist_ok=True)

    if not target.exists():
        shutil.copy2(source, target)

    copied, copied_ok, copied_reason = load_verified_json(target)
    if not copied_ok or copied["_provenance"]["sha256"] != digest:
        return {
            "status": "blocked",
            "reason": f"snapshot-verification-{copied_reason}",
            "source": str(source),
            "target": str(target),
        }

    record = attach_provenance(
        {
            "schema": "aegis-lkg-version/v1",
            "created_at": now_iso(),
            "kind": kind,
            "sha256": digest,
            "artifact": str(target),
            "source": str(source),
            "metadata": metadata or {},
        },
        kind="lkg-version-record",
        parent_sha256=digest,
        parent_kind=value["_provenance"].get("kind"),
    )
    _atomic_json(target_dir / "record.json", record)
    return {"status": "snapshotted", "sha256": digest, "artifact": str(target)}


def promote(source: Path, *, kind: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snapshot_artifact(source, kind=kind, metadata=metadata)
    if snap.get("status") != "snapshotted":
        return snap

    pointer = attach_provenance(
        {
            "schema": "aegis-lkg-pointer/v1",
            "updated_at": now_iso(),
            "kind": kind,
            "sha256": snap["sha256"],
            "artifact": snap["artifact"],
            "metadata": metadata or {},
        },
        kind="lkg-pointer",
        parent_sha256=snap["sha256"],
        parent_kind=kind,
    )
    _atomic_json(POINTER, pointer)

    ROOT.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "event": "promote",
            "at": pointer["updated_at"],
            "kind": kind,
            "sha256": snap["sha256"],
            "artifact": snap["artifact"],
        }, sort_keys=True) + "\n")

    return {"status": "promoted", "pointer": pointer}


def current() -> dict[str, Any]:
    value, ok, reason = load_verified_json(POINTER)
    if not ok:
        return {"status": "missing-or-invalid", "reason": reason}
    artifact = Path(value.get("artifact", ""))
    artifact_value, artifact_ok, artifact_reason = load_verified_json(artifact)
    if not artifact_ok:
        return {"status": "broken", "reason": artifact_reason, "pointer": value}
    expected = value.get("sha256")
    actual = artifact_value.get("_provenance", {}).get("sha256")
    if expected != actual:
        return {"status": "broken", "reason": "pointer-artifact-hash-mismatch", "pointer": value}
    return {"status": "ok", "pointer": value, "artifact": artifact_value}


def restore(destination: Path) -> dict[str, Any]:
    cur = current()
    if cur.get("status") != "ok":
        return {"status": "blocked", "reason": cur.get("reason", "no-valid-lkg")}
    pointer = cur["pointer"]
    source = Path(pointer["artifact"])
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        existing, ok, _ = load_verified_json(destination)
        if ok and existing.get("_provenance", {}).get("sha256") == pointer["sha256"]:
            return {"status": "already-current", "sha256": pointer["sha256"]}

    tmp = destination.with_suffix(destination.suffix + ".rollback.tmp")
    shutil.copy2(source, tmp)
    verify, ok, reason = load_verified_json(tmp)
    if not ok or verify.get("_provenance", {}).get("sha256") != pointer["sha256"]:
        tmp.unlink(missing_ok=True)
        return {"status": "blocked", "reason": f"rollback-verification-{reason}"}
    tmp.replace(destination)

    with HISTORY.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "event": "restore",
            "at": now_iso(),
            "sha256": pointer["sha256"],
            "destination": str(destination),
        }, sort_keys=True) + "\n")
    return {"status": "restored", "sha256": pointer["sha256"], "destination": str(destination)}


def main() -> int:
    p = argparse.ArgumentParser(description="AEGIS last-known-good state manager.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("promote")
    sp.add_argument("--source", type=Path, required=True)
    sp.add_argument("--kind", required=True)

    sr = sub.add_parser("restore")
    sr.add_argument("--destination", type=Path, required=True)

    sub.add_parser("status")

    args = p.parse_args()
    if args.cmd == "promote":
        result = promote(args.source, kind=args.kind)
    elif args.cmd == "restore":
        result = restore(args.destination)
    else:
        result = current()
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {"promoted", "restored", "already-current", "ok"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
