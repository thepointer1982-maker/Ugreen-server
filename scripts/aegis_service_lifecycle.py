#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aegis_provenance import attach_provenance, verify_provenance

REPO_ROOT = Path(
    os.environ.get("AEGIS_REPO_ROOT", Path(__file__).resolve().parents[1])
).resolve()
MANIFEST_FILE = Path(
    os.environ.get(
        "AEGIS_LIFECYCLE_MANIFEST",
        REPO_ROOT / "config" / "lifecycle" / "aegis-services.json",
    )
)
STATE_DIR = Path(
    os.environ.get(
        "AEGIS_LIFECYCLE_STATE_DIR",
        Path.home() / ".local/state/aegis-lifecycle",
    )
)
STATUS_FILE = STATE_DIR / "status.json"


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat()


def run(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            cmd,
            124,
            exc.stdout if isinstance(exc.stdout, str) else "",
            exc.stderr if isinstance(exc.stderr, str) else "timeout",
        )
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 126, "", str(exc))


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    finally:
        try:
            os.unlink(raw)
        except FileNotFoundError:
            pass


def version_tuple(value: str | None) -> tuple[int, ...] | None:
    if not value:
        return None
    m = re.search(r"(?<!\d)(\d+(?:\.\d+){1,3})(?!\d)", value)
    if not m:
        return None
    return tuple(int(x) for x in m.group(1).split("."))


def version_lt(a: str | None, b: str | None) -> bool | None:
    av = version_tuple(a)
    bv = version_tuple(b)
    if av is None or bv is None:
        return None
    width = max(len(av), len(bv))
    av = av + (0,) * (width - len(av))
    bv = bv + (0,) * (width - len(bv))
    return av < bv


def release_age_days(value: str | None) -> int | None:
    if not value:
        return None
    try:
        released = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, int((now() - released).total_seconds() // 86400))


def command_version(command: str, args: list[str]) -> dict[str, Any]:
    path = shutil.which(command)
    if not path:
        return {
            "installed": False,
            "path": None,
            "version_raw": None,
            "version": None,
        }
    cp = run([command, *args], timeout=10)
    raw = (cp.stdout or cp.stderr).strip()[-1000:]
    parsed = version_tuple(raw)
    return {
        "installed": True,
        "path": path,
        "rc": cp.returncode,
        "version_raw": raw,
        "version": ".".join(map(str, parsed)) if parsed else None,
    }


def python_package_version(
    package: str,
    python_bin: str | None = None,
) -> dict[str, Any]:
    executable = python_bin or sys.executable
    cp = run(
        [
            executable,
            "-c",
            (
                "import importlib.metadata as m; "
                f"print(m.version({package!r}))"
            ),
        ],
        timeout=10,
    )
    if cp.returncode != 0:
        return {"installed": False, "version": None, "rc": cp.returncode}
    raw = cp.stdout.strip()
    return {
        "installed": True,
        "version": raw,
        "version_raw": raw,
        "rc": cp.returncode,
    }


def user_unit_state(unit: str) -> dict[str, Any]:
    if not shutil.which("systemctl"):
        return {"available": False, "reason": "systemctl-missing"}
    cp = run(
        [
            "systemctl",
            "--user",
            "show",
            unit,
            "--no-page",
            "--property=LoadState,ActiveState,SubState,UnitFileState,FragmentPath",
        ],
        timeout=10,
    )
    if cp.returncode != 0:
        return {"available": False, "reason": "user-systemd-unavailable"}
    values: dict[str, str] = {}
    for line in cp.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    fragment = values.get("FragmentPath")
    age_days = None
    if fragment:
        try:
            age_days = int(
                (now().timestamp() - Path(fragment).stat().st_mtime) // 86400
            )
        except OSError:
            pass
    return {
        "available": True,
        "load": values.get("LoadState"),
        "active": values.get("ActiveState"),
        "sub": values.get("SubState"),
        "enabled": values.get("UnitFileState"),
        "fragment": fragment,
        "local_fragment_age_days": age_days,
    }


def docker_container_version(name: str) -> dict[str, Any]:
    if not shutil.which("docker"):
        return {"installed": False, "reason": "docker-missing"}
    cp = run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Config.Image}}|{{.State.Status}}|{{.Created}}",
            name,
        ],
        timeout=10,
    )
    if cp.returncode != 0:
        return {"installed": False, "reason": "container-not-found"}
    raw = cp.stdout.strip()
    parts = raw.split("|", 2)
    image = parts[0] if len(parts) >= 1 else None
    state = parts[1] if len(parts) >= 2 else None
    created = parts[2] if len(parts) >= 3 else None
    version = None
    if image:
        m = re.search(r":v?(\d+(?:\.\d+){1,3})(?:@|$)", image)
        if m:
            version = m.group(1)
    return {
        "installed": True,
        "image": image,
        "state": state,
        "created": created,
        "version": version,
    }


def inspect_service(spec: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": spec.get("id"),
        "class": spec.get("class"),
        "kind": spec.get("kind"),
        "required": bool(spec.get("required")),
        "known_current": spec.get("known_current"),
        "minimum_supported": spec.get("minimum_supported"),
        "legacy_below": spec.get("legacy_below"),
        "release_date": spec.get("release_date"),
    }
    kind = spec.get("kind")
    if kind == "systemd-user":
        result["runtime"] = user_unit_state(str(spec.get("unit") or ""))
        result["installed"] = result["runtime"].get("load") == "loaded"
        result["active"] = result["runtime"].get("active") == "active"
    elif kind == "command":
        runtime = command_version(
            str(spec.get("command") or ""),
            [str(x) for x in spec.get("args", [])],
        )
        result["runtime"] = runtime
        result["installed"] = runtime.get("installed", False)
        result["version"] = runtime.get("version")
    elif kind == "python-package":
        python_bin = None
        if spec.get("id") == "mcp-python":
            mcp_state = read_json(
                Path.home() / ".local/state/aegis-mcp/runtime.json"
            )
            candidate = mcp_state.get("python")
            if isinstance(candidate, str) and Path(candidate).is_file():
                python_bin = candidate
            else:
                venv_python = (
                    Path.home()
                    / ".local/share/aegis-mcp/venv/bin/python"
                )
                if venv_python.is_file():
                    python_bin = str(venv_python)
        runtime = python_package_version(
            str(spec.get("package") or ""),
            python_bin=python_bin,
        )
        runtime["python"] = python_bin or sys.executable
        result["runtime"] = runtime
        result["installed"] = runtime.get("installed", False)
        result["version"] = runtime.get("version")
    elif kind == "docker-image":
        runtime = docker_container_version(str(spec.get("container") or ""))
        result["runtime"] = runtime
        result["installed"] = runtime.get("installed", False)
        result["version"] = runtime.get("version")
    elif kind == "binary":
        command = str(spec.get("command") or "")
        path = shutil.which(command)
        if not path and spec.get("id") == "github-runner":
            candidate = (
                Path.home()
                / "actions-runner-aegis-v2/bin/Runner.Listener"
            )
            if candidate.is_file():
                path = str(candidate)
        runtime = {
            "installed": bool(path),
            "path": path,
            "version": None,
        }
        if path:
            cp = run([path, "--version"], timeout=10)
            raw = (cp.stdout or cp.stderr).strip()[-1000:]
            parsed = version_tuple(raw)
            runtime["version_raw"] = raw
            runtime["version"] = (
                ".".join(map(str, parsed))
                if parsed
                else None
            )
            result["version"] = runtime["version"]
        result["runtime"] = runtime
        result["installed"] = bool(path)
    else:
        result["installed"] = False
        result["runtime"] = {"reason": "unsupported-kind"}

    version = result.get("version")
    min_supported = spec.get("minimum_supported")
    legacy_below = spec.get("legacy_below")
    known_current = spec.get("known_current")
    result["below_minimum"] = (
        version_lt(str(version), str(min_supported))
        if version and min_supported
        else None
    )
    result["legacy"] = (
        version_lt(str(version), str(legacy_below))
        if version and legacy_below
        else False
    )
    result["behind_known_current"] = (
        version_lt(str(version), str(known_current))
        if version and known_current
        else None
    )
    result["known_release_age_days"] = release_age_days(
        str(spec.get("release_date")) if spec.get("release_date") else None
    )

    status = "healthy"
    reasons: list[str] = []
    if result["required"] and not result["installed"]:
        status = "blocked"
        reasons.append("required-service-missing")
    elif not result["required"] and not result["installed"]:
        status = "optional-missing"
        reasons.append("optional-service-not-installed")

    if kind == "systemd-user" and result.get("installed") and not result.get("active"):
        status = "degraded" if result["required"] else "optional-inactive"
        reasons.append("unit-not-active")

    if result.get("legacy") is True:
        status = "blocked" if result["class"] in {"core", "core-ai"} else "legacy"
        reasons.append("legacy-version")
    elif result.get("below_minimum") is True:
        status = "blocked" if result["required"] else "outdated"
        reasons.append("below-minimum-supported")
    elif result.get("behind_known_current") is True:
        if status == "healthy":
            status = "review"
        reasons.append("behind-reviewed-current")

    result["status"] = status
    result["reasons"] = reasons
    return result


def integration_coverage(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows = manifest.get("integration_coverage", [])
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        evidence = item.get("evidence", [])
        if isinstance(evidence, list):
            item["evidence_present"] = {
                str(path): (REPO_ROOT / str(path)).exists()
                for path in evidence
            }
        result.append(item)
    return result


def main() -> int:
    manifest = read_json(MANIFEST_FILE)
    if manifest.get("schema") != "aegis-service-lifecycle/v1":
        print("invalid lifecycle manifest", file=sys.stderr)
        return 3

    specs = manifest.get("services", [])
    if not isinstance(specs, list):
        return 3

    services = [
        inspect_service(spec)
        for spec in specs
        if isinstance(spec, dict)
    ]

    stale_after = int(manifest.get("stale_release_after_days") or 365)
    for service in services:
        age = service.get("known_release_age_days")
        if isinstance(age, int) and age > stale_after:
            if service.get("status") == "healthy":
                service["status"] = "review"
            service.setdefault("reasons", []).append(
                "reviewed-release-baseline-stale"
            )

    reviewed_at = manifest.get("reviewed_at")
    manifest_age_days = release_age_days(
        str(reviewed_at) if reviewed_at else None
    )
    review_after = int(manifest.get("baseline_review_after_days") or 60)
    baseline_stale = (
        isinstance(manifest_age_days, int)
        and manifest_age_days > review_after
    )

    blocked = [s["id"] for s in services if s.get("status") == "blocked"]
    legacy = [s["id"] for s in services if s.get("status") == "legacy"]
    review = [
        s["id"]
        for s in services
        if s.get("status") in {"review", "outdated", "degraded"}
    ]

    health = "healthy"
    if blocked:
        health = "blocked"
    elif legacy or review or baseline_stale:
        health = "degraded"

    report = attach_provenance(
        {
            "schema": "aegis-service-lifecycle-status/v1",
            "generated_at": now_iso(),
            "health": health,
            "blocked": blocked,
            "legacy": legacy,
            "review": review,
            "services": services,
            "integration_coverage": integration_coverage(manifest),
            "policy": manifest.get("policy", {}),
            "baseline": {
                "reviewed_at": reviewed_at,
                "age_days": manifest_age_days,
                "review_after_days": review_after,
                "stale": baseline_stale,
            },
            "guardrails": {
                "read_only": True,
                "auto_update_external_software": False,
                "auto_remove_services": False,
                "auto_enable_optional_service": False,
            },
        },
        kind="service-lifecycle-status",
    )
    atomic_json(STATUS_FILE, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if health in {"healthy", "degraded"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
