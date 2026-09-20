#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aegis_provenance import attach_provenance

MINIMUM = (1, 17, 0)
REVIEWED = "1.18.31"
STATE_DIR = Path(
    os.environ.get(
        "AEGIS_OPENCODE_STATE_DIR",
        Path.home() / ".local/state/aegis-opencode",
    )
)
STATE_FILE = STATE_DIR / "status.json"


def parse_version(value: str) -> tuple[int, ...] | None:
    match = re.search(r"(?<!\d)(\d+(?:\.\d+){1,3})(?!\d)", value)
    if not match:
        return None
    return tuple(int(x) for x in match.group(1).split("."))


def normalized(value: tuple[int, ...]) -> tuple[int, ...]:
    return value + (0,) * (3 - len(value))


def inspect(binary: str | None = None) -> dict[str, Any]:
    path = binary or shutil.which("opencode")
    if not path:
        return {
            "schema": "aegis-opencode-status/v1",
            "health": "optional-missing",
            "reason": "opencode-not-installed",
            "binary": None,
            "version": None,
            "minimum_supported": ".".join(map(str, MINIMUM)),
            "reviewed_current": REVIEWED,
            "legacy": False,
            "modern_fallback_allowed": False,
        }

    try:
        cp = subprocess.run(
            [path, "--version"],
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
        )
    except Exception as exc:
        return {
            "schema": "aegis-opencode-status/v1",
            "health": "blocked",
            "reason": f"version-check-failed:{type(exc).__name__}",
            "binary": path,
            "version": None,
            "minimum_supported": ".".join(map(str, MINIMUM)),
            "reviewed_current": REVIEWED,
            "legacy": True,
            "modern_fallback_allowed": False,
        }

    raw = (cp.stdout or cp.stderr).strip()[-1000:]
    parsed = parse_version(raw)
    if parsed is None:
        return {
            "schema": "aegis-opencode-status/v1",
            "health": "blocked",
            "reason": "version-unparseable",
            "binary": path,
            "version_raw": raw,
            "version": None,
            "minimum_supported": ".".join(map(str, MINIMUM)),
            "reviewed_current": REVIEWED,
            "legacy": True,
            "modern_fallback_allowed": False,
        }

    version = ".".join(map(str, parsed))
    legacy = normalized(parsed) < MINIMUM
    return {
        "schema": "aegis-opencode-status/v1",
        "health": "legacy" if legacy else "healthy",
        "reason": "legacy-version" if legacy else "modern-opencode-ready",
        "binary": path,
        "version_raw": raw,
        "version": version,
        "minimum_supported": ".".join(map(str, MINIMUM)),
        "reviewed_current": REVIEWED,
        "legacy": legacy,
        "modern_fallback_allowed": not legacy,
        "upstream": "anomalyco/opencode",
        "legacy_upstream": "opencode-ai/opencode",
    }


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary")
    parser.add_argument("--require-modern", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    report = attach_provenance(
        inspect(args.binary),
        kind="opencode-status",
    )
    if not args.no_write:
        atomic_json(STATE_FILE, report)
    print(json.dumps(report, ensure_ascii=False))

    if args.require_modern:
        return 0 if report.get("modern_fallback_allowed") is True else 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
