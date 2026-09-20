#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PROVENANCE_KEY = "_provenance"


def canonical_payload(value: dict[str, Any]) -> bytes:
    payload = dict(value)
    payload.pop(PROVENANCE_KEY, None)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_payload(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(value)).hexdigest()


def attach_provenance(
    value: dict[str, Any],
    *,
    kind: str,
    parent_sha256: str | None = None,
    parent_kind: str | None = None,
) -> dict[str, Any]:
    result = dict(value)
    result.pop(PROVENANCE_KEY, None)
    digest = sha256_payload(result)
    result[PROVENANCE_KEY] = {
        "algorithm": "sha256",
        "kind": kind,
        "sha256": digest,
        "parent_sha256": parent_sha256,
        "parent_kind": parent_kind,
    }
    return result


def verify_provenance(value: dict[str, Any]) -> tuple[bool, str]:
    prov = value.get(PROVENANCE_KEY)
    if not isinstance(prov, dict):
        return False, "provenance-missing"
    if prov.get("algorithm") != "sha256":
        return False, "unsupported-algorithm"
    expected = prov.get("sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        return False, "invalid-sha256"
    actual = sha256_payload(value)
    if actual != expected:
        return False, "sha256-mismatch"
    return True, "verified"


def load_verified_json(path: Path) -> tuple[dict[str, Any], bool, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, False, f"unreadable:{type(exc).__name__}"
    if not isinstance(value, dict):
        return {}, False, "not-object"
    ok, reason = verify_provenance(value)
    return value, ok, reason


__all__ = [
    "PROVENANCE_KEY",
    "attach_provenance",
    "canonical_payload",
    "load_verified_json",
    "sha256_payload",
    "verify_provenance",
]
