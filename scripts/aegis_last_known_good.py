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
PROVISIONAL = ROOT / "provisional.json"
PROBATION_HISTORY = ROOT / "probation-history.jsonl"
ACTIVE = ROOT / "active.json"
PREVIOUS_ACTIVE = ROOT / "previous-active.json"
ACTIVE_HISTORY = ROOT / "active-history.jsonl"
ACTIVE_BACKUPS = ROOT / "active-backups"
DEFAULT_RESTORE_ROOTS = [
    Path.home() / ".local/state/aegis-ai-miner",
    Path.home() / ".local/state/aegis-guardian",
    Path("/opt/aegis"),
]


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



def stage_provisional(
    source: Path,
    *,
    kind: str,
    metadata: dict[str, Any] | None = None,
    required_passes: int = 3,
    max_failures: int = 1,
) -> dict[str, Any]:
    if required_passes <= 0 or max_failures < 0:
        return {"status": "blocked", "reason": "invalid-probation-policy"}

    snap = snapshot_artifact(source, kind=kind, metadata=metadata)
    if snap.get("status") != "snapshotted":
        return snap

    existing = provisional_status()
    if existing.get("status") == "ok":
        existing_sha = existing.get("provisional", {}).get("sha256")
        if existing_sha == snap["sha256"]:
            return {
                "status": "provisional",
                "provisional": existing["provisional"],
                "idempotent": True,
            }
        return {
            "status": "blocked",
            "reason": "provisional-already-in-progress",
            "existing_sha256": existing_sha,
            "candidate_sha256": snap["sha256"],
        }

    previous = current()
    previous_sha = (
        previous.get("pointer", {}).get("sha256")
        if previous.get("status") == "ok"
        else None
    )
    provisional = attach_provenance(
        {
            "schema": "aegis-provisional/v1",
            "created_at": now_iso(),
            "kind": kind,
            "sha256": snap["sha256"],
            "artifact": snap["artifact"],
            "metadata": metadata or {},
            "previous_lkg_sha256": previous_sha,
            "required_passes": required_passes,
            "max_failures": max_failures,
            "passes": 0,
            "failures": 0,
            "state": "provisional",
        },
        kind="provisional-pointer",
        parent_sha256=snap["sha256"],
        parent_kind=kind,
    )
    _atomic_json(PROVISIONAL, provisional)
    ROOT.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "event": "stage_provisional",
            "at": provisional["created_at"],
            "kind": kind,
            "sha256": snap["sha256"],
            "previous_lkg_sha256": previous_sha,
        }, sort_keys=True) + "\n")
    return {"status": "provisional", "provisional": provisional}


def provisional_status() -> dict[str, Any]:
    value, ok, reason = load_verified_json(PROVISIONAL)
    if not ok:
        return {"status": "missing-or-invalid", "reason": reason}
    artifact = Path(value.get("artifact", ""))
    artifact_value, artifact_ok, artifact_reason = load_verified_json(artifact)
    if not artifact_ok:
        return {"status": "broken", "reason": artifact_reason, "provisional": value}
    if artifact_value.get("_provenance", {}).get("sha256") != value.get("sha256"):
        return {"status": "broken", "reason": "provisional-artifact-hash-mismatch", "provisional": value}
    return {"status": "ok", "provisional": value, "artifact": artifact_value}


def observe_provisional(
    *,
    passed: bool,
    evidence: dict[str, Any] | None = None,
    rollback_destination: Path | None = None,
) -> dict[str, Any]:
    state = provisional_status()
    if state.get("status") != "ok":
        return {"status": "blocked", "reason": state.get("reason", "no-valid-provisional")}

    provisional = dict(state["provisional"])
    passes = int(provisional.get("passes", 0))
    failures = int(provisional.get("failures", 0))
    if passed:
        passes += 1
    else:
        failures += 1

    event = {
        "event": "probation_observation",
        "at": now_iso(),
        "sha256": provisional.get("sha256"),
        "passed": bool(passed),
        "passes": passes,
        "failures": failures,
        "evidence": evidence or {},
    }
    ROOT.mkdir(parents=True, exist_ok=True)
    with PROBATION_HISTORY.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    required_passes = int(provisional.get("required_passes", 3))
    max_failures = int(provisional.get("max_failures", 1))

    if failures > max_failures:
        result: dict[str, Any] = {
            "status": "rejected",
            "reason": "probation-regression",
            "passes": passes,
            "failures": failures,
        }
        if rollback_destination is not None:
            result["rollback"] = {
                "status": "not-needed",
                "reason": "provisional-was-never-activated",
            }
        provisional["state"] = "rejected"
        provisional["passes"] = passes
        provisional["failures"] = failures
        provisional = attach_provenance(
            {k: v for k, v in provisional.items() if k != "_provenance"},
            kind="provisional-pointer",
            parent_sha256=provisional["sha256"],
            parent_kind=provisional["kind"],
        )
        _atomic_json(PROVISIONAL, provisional)
        return result

    if passes >= required_passes:
        source = Path(provisional["artifact"])
        promoted = promote(
            source,
            kind=str(provisional["kind"]),
            metadata={
                **(provisional.get("metadata") or {}),
                "probation_passes": passes,
                "probation_failures": failures,
                "probation_confirmed_at": now_iso(),
            },
        )
        if promoted.get("status") != "promoted":
            return {
                "status": "blocked",
                "reason": "probation-promotion-failed",
                "promotion": promoted,
            }
        try:
            PROVISIONAL.unlink()
        except FileNotFoundError:
            pass
        return {
            "status": "confirmed",
            "passes": passes,
            "failures": failures,
            "promotion": promoted,
        }

    provisional["passes"] = passes
    provisional["failures"] = failures
    provisional = attach_provenance(
        {k: v for k, v in provisional.items() if k != "_provenance"},
        kind="provisional-pointer",
        parent_sha256=provisional["sha256"],
        parent_kind=provisional["kind"],
    )
    _atomic_json(PROVISIONAL, provisional)
    return {
        "status": "probation",
        "passes": passes,
        "failures": failures,
        "required_passes": required_passes,
        "max_failures": max_failures,
    }



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



def _backup_existing_active(destination: Path) -> dict[str, Any]:
    if not destination.exists():
        return {"status": "none"}

    value, ok, reason = load_verified_json(destination)
    if not ok:
        return {
            "status": "blocked",
            "reason": f"existing-active-{reason}",
            "destination": str(destination),
        }

    digest = value["_provenance"]["sha256"]
    backup_dir = ACTIVE_BACKUPS / digest
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / destination.name
    if not backup.exists():
        shutil.copy2(destination, backup)

    copied, copied_ok, copied_reason = load_verified_json(backup)
    if not copied_ok or copied["_provenance"]["sha256"] != digest:
        return {
            "status": "blocked",
            "reason": f"active-backup-{copied_reason}",
        }

    pointer = attach_provenance(
        {
            "schema": "aegis-previous-active/v1",
            "created_at": now_iso(),
            "sha256": digest,
            "artifact": str(backup),
            "destination": str(destination),
        },
        kind="previous-active-pointer",
        parent_sha256=digest,
        parent_kind=value["_provenance"].get("kind"),
    )
    _atomic_json(PREVIOUS_ACTIVE, pointer)
    return {
        "status": "backed-up",
        "sha256": digest,
        "artifact": str(backup),
    }


def activate_current_lkg(
    destination: Path,
    *,
    required_health_passes: int = 2,
) -> dict[str, Any]:
    if required_health_passes <= 0:
        return {"status": "blocked", "reason": "invalid-activation-policy"}
    if not _destination_allowed(destination):
        return {
            "status": "blocked",
            "reason": "destination-outside-allowlist",
            "destination": str(destination),
        }

    cur = current()
    if cur.get("status") != "ok":
        return {
            "status": "blocked",
            "reason": cur.get("reason", "no-valid-lkg"),
        }

    existing_active = active_status()
    if existing_active.get("status") == "ok":
        existing_state = existing_active.get("active", {}).get("state")
        existing_sha = existing_active.get("active", {}).get("sha256")
        existing_destination = existing_active.get("active", {}).get("destination")
        if existing_state == "validating":
            if (
                existing_sha == cur["pointer"].get("sha256")
                and existing_destination == str(destination)
            ):
                return {
                    "status": "activated-validating",
                    "active": existing_active["active"],
                    "idempotent": True,
                }
            return {
                "status": "blocked",
                "reason": "activation-already-validating",
                "existing_sha256": existing_sha,
            }

    pointer = cur["pointer"]
    source = Path(pointer["artifact"])
    backup = _backup_existing_active(destination)
    if backup.get("status") == "blocked":
        return backup

    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".activate.tmp")
    shutil.copy2(source, tmp)
    copied, ok, reason = load_verified_json(tmp)
    if not ok or copied.get("_provenance", {}).get("sha256") != pointer["sha256"]:
        tmp.unlink(missing_ok=True)
        return {
            "status": "blocked",
            "reason": f"activation-verification-{reason}",
        }
    tmp.replace(destination)

    active = attach_provenance(
        {
            "schema": "aegis-active-state/v1",
            "activated_at": now_iso(),
            "state": "validating",
            "sha256": pointer["sha256"],
            "destination": str(destination),
            "previous_sha256": backup.get("sha256"),
            "required_health_passes": required_health_passes,
            "health_passes": 0,
            "health_failures": 0,
        },
        kind="active-state",
        parent_sha256=pointer["sha256"],
        parent_kind=pointer.get("kind"),
    )
    _atomic_json(ACTIVE, active)
    ROOT.mkdir(parents=True, exist_ok=True)
    with ACTIVE_HISTORY.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "event": "activate",
            "at": active["activated_at"],
            "sha256": pointer["sha256"],
            "destination": str(destination),
            "previous_sha256": backup.get("sha256"),
        }, sort_keys=True) + "\n")
    return {
        "status": "activated-validating",
        "active": active,
        "backup": backup,
    }


def active_status() -> dict[str, Any]:
    value, ok, reason = load_verified_json(ACTIVE)
    if not ok:
        return {"status": "missing-or-invalid", "reason": reason}
    destination = Path(value.get("destination", ""))
    artifact, artifact_ok, artifact_reason = load_verified_json(destination)
    if not artifact_ok:
        return {
            "status": "broken",
            "reason": f"active-artifact-{artifact_reason}",
            "active": value,
        }
    if artifact.get("_provenance", {}).get("sha256") != value.get("sha256"):
        return {
            "status": "broken",
            "reason": "active-artifact-hash-mismatch",
            "active": value,
        }
    return {"status": "ok", "active": value, "artifact": artifact}


def rollback_previous_active() -> dict[str, Any]:
    previous, ok, reason = load_verified_json(PREVIOUS_ACTIVE)
    if not ok:
        return {"status": "blocked", "reason": f"previous-active-{reason}"}

    source = Path(previous.get("artifact", ""))
    destination = Path(previous.get("destination", ""))
    if not _destination_allowed(destination):
        return {
            "status": "blocked",
            "reason": "previous-active-destination-outside-allowlist",
        }

    value, source_ok, source_reason = load_verified_json(source)
    if not source_ok:
        return {
            "status": "blocked",
            "reason": f"previous-active-artifact-{source_reason}",
        }
    expected = previous.get("sha256")
    if value.get("_provenance", {}).get("sha256") != expected:
        return {
            "status": "blocked",
            "reason": "previous-active-hash-mismatch",
        }

    tmp = destination.with_suffix(destination.suffix + ".rollback-active.tmp")
    shutil.copy2(source, tmp)
    copied, copied_ok, copied_reason = load_verified_json(tmp)
    if not copied_ok or copied.get("_provenance", {}).get("sha256") != expected:
        tmp.unlink(missing_ok=True)
        return {
            "status": "blocked",
            "reason": f"active-rollback-verification-{copied_reason}",
        }
    tmp.replace(destination)

    rolled = attach_provenance(
        {
            "schema": "aegis-active-state/v1",
            "activated_at": now_iso(),
            "state": "rolled-back",
            "sha256": expected,
            "destination": str(destination),
            "previous_sha256": None,
            "required_health_passes": 0,
            "health_passes": 0,
            "health_failures": 0,
        },
        kind="active-state",
        parent_sha256=expected,
        parent_kind=value["_provenance"].get("kind"),
    )
    _atomic_json(ACTIVE, rolled)
    with ACTIVE_HISTORY.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "event": "rollback_previous_active",
            "at": rolled["activated_at"],
            "sha256": expected,
            "destination": str(destination),
        }, sort_keys=True) + "\n")
    return {
        "status": "rolled-back",
        "sha256": expected,
        "destination": str(destination),
    }


def observe_active_health(*, passed: bool, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    state = active_status()
    if state.get("status") != "ok":
        return {"status": "blocked", "reason": state.get("reason", "no-valid-active")}

    active = dict(state["active"])
    if active.get("state") not in {"validating", "stable"}:
        return {"status": str(active.get("state") or "unknown")}

    passes = int(active.get("health_passes", 0))
    failures = int(active.get("health_failures", 0))
    if passed:
        passes += 1
    else:
        failures += 1

    with ACTIVE_HISTORY.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "event": "active_health",
            "at": now_iso(),
            "sha256": active.get("sha256"),
            "passed": bool(passed),
            "passes": passes,
            "failures": failures,
            "evidence": evidence or {},
        }, ensure_ascii=False, sort_keys=True) + "\n")

    if not passed and active.get("state") == "validating":
        rollback = rollback_previous_active()
        return {
            "status": "regression",
            "passes": passes,
            "failures": failures,
            "rollback": rollback,
        }

    required = int(active.get("required_health_passes", 2))
    if passes >= required:
        active["state"] = "stable"
    active["health_passes"] = passes
    active["health_failures"] = failures
    active = attach_provenance(
        {k: v for k, v in active.items() if k != "_provenance"},
        kind="active-state",
        parent_sha256=active["sha256"],
        parent_kind="activated-artifact",
    )
    _atomic_json(ACTIVE, active)
    return {
        "status": str(active["state"]),
        "passes": passes,
        "failures": failures,
        "required_health_passes": required,
    }



def _allowed_restore_roots() -> list[Path]:
    raw = os.environ.get("AEGIS_LKG_RESTORE_ROOTS")
    roots = [Path(p).expanduser() for p in raw.split(os.pathsep)] if raw else DEFAULT_RESTORE_ROOTS
    resolved: list[Path] = []
    for root in roots:
        try:
            resolved.append(root.resolve(strict=False))
        except OSError:
            continue
    return resolved


def _destination_allowed(destination: Path) -> bool:
    try:
        resolved = destination.expanduser().resolve(strict=False)
    except OSError:
        return False
    for root in _allowed_restore_roots():
        if resolved == root or root in resolved.parents:
            return True
    return False


def restore(destination: Path) -> dict[str, Any]:
    if not _destination_allowed(destination):
        return {
            "status": "blocked",
            "reason": "destination-outside-allowlist",
            "destination": str(destination),
        }
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
