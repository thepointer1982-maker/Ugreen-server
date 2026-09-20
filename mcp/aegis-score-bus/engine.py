from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

EVIDENCE_CLASSES = {
    "REAL_DEVICE_MEASUREMENT",
    "VERIFIED_EXPORT",
    "TEST_FIXTURE",
    "SYNTHETIC",
    "HEURISTIC",
    "PROJECTED_TARGET",
    "TEMPLATE",
    "ARCHIVE_CLAIM",
    "UNKNOWN",
}
REAL_EVIDENCE = {"REAL_DEVICE_MEASUREMENT", "VERIFIED_EXPORT"}

STATUS_KEYS = {
    "a1", "a3", "health", "stability", "autonomy", "confidence", "learning",
    "recovery", "failover", "network", "nas", "agents", "devices", "release_status",
    "a1_status", "a3_status", "promotion", "a1_a3_promotion", "actual_device_tests",
    "live_device_connections", "faktor_50_verified",
}
RISK_WORDS = {
    "regression", "degraded", "failed", "failure", "blocked", "not_run", "not run",
    "down", "unhealthy", "error", "stale", "mismatch", "invalid",
}
FIXTURE_MARKERS = ("fixture", "test", "tests", "mock", "sample", "example", "demo")
TEMPLATE_MARKERS = ("template", "skeleton")
TARGET_MARKERS = ("target", "projected", "projection", "planned", "goal")
ARCHIVE_MARKERS = ("archive", "legacy", "historic", "readme")


@dataclass(frozen=True)
class ScoreRecord:
    key: str
    name: str
    value: float | None
    status: str | None
    source: str
    path: str
    observed_at: str | None
    file_sha256: str
    git_commit: str | None
    export_session: str | None
    evidence_class: str
    provenance_verified: bool
    hash_verified: bool | None
    freshness: str
    previous_value: float | None
    previous_status: str | None
    delta: float | None
    changed: bool
    new: bool
    counts_as_real_improvement: bool
    warning: str | None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_status(value: Any) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _explicit_evidence(obj: Any) -> str | None:
    if not isinstance(obj, dict):
        return None
    for key in ("evidence_class", "evidence_type", "classification"):
        value = obj.get(key)
        if isinstance(value, str) and value.upper() in EVIDENCE_CLASSES:
            return value.upper()
    return None


def _path_has_marker(path: Path, markers: tuple[str, ...]) -> bool:
    tokens: list[str] = []
    for part in path.parts:
        tokens.extend(t for t in re.split(r"[^a-z0-9]+", part.lower()) if t)
    return any(marker in tokens for marker in markers)


def _classify(path: Path, root_obj: Any, hash_verified: bool | None, export_session: str | None) -> str:
    explicit = _explicit_evidence(root_obj)
    if _path_has_marker(path, FIXTURE_MARKERS):
        return "TEST_FIXTURE"
    if _path_has_marker(path, TEMPLATE_MARKERS):
        return "TEMPLATE"
    if _path_has_marker(path, TARGET_MARKERS):
        return "PROJECTED_TARGET"
    if _path_has_marker(path, ARCHIVE_MARKERS):
        return "ARCHIVE_CLAIM"
    if explicit:
        if explicit == "VERIFIED_EXPORT" and not (hash_verified is True and export_session):
            return "UNKNOWN"
        if explicit == "REAL_DEVICE_MEASUREMENT":
            if isinstance(root_obj, dict) and any(root_obj.get(k) for k in ("device_id", "device", "observed_at", "measured_at")):
                return explicit
            return "UNKNOWN"
        return explicit
    if hash_verified is True and export_session:
        return "VERIFIED_EXPORT"
    if _path_has_marker(path, ("synthetic",)):
        return "SYNTHETIC"
    if _path_has_marker(path, ("heuristic",)):
        return "HEURISTIC"
    return "UNKNOWN"


def _iter_scalars(obj: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                yield from _iter_scalars(v, key)
            else:
                yield key, v
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = f"{prefix}[{i}]"
            if isinstance(v, (dict, list)):
                yield from _iter_scalars(v, key)
            else:
                yield key, v


def _candidate_metric(key: str, value: Any) -> bool:
    leaf = re.sub(r"\[\d+\]$", "", key.split(".")[-1]).lower()
    if "score" in leaf:
        return _number(value) is not None or _normalize_status(value) is not None
    return leaf in STATUS_KEYS and (_number(value) is not None or _normalize_status(value) is not None)


def _extract_times(obj: Any) -> datetime | None:
    if not isinstance(obj, dict):
        return None
    for k in ("observed_at", "measured_at", "generated_at", "timestamp", "created_at", "updated_at"):
        dt = _parse_time(obj.get(k))
        if dt:
            return dt
    return None


def _extract_commit(obj: Any) -> str | None:
    if not isinstance(obj, dict):
        return None
    for k in ("commit", "commit_sha", "git_commit", "sha"):
        value = obj.get(k)
        if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{7,64}", value.strip()):
            return value.strip()
    return None


def _extract_export_session(obj: Any) -> str | None:
    if not isinstance(obj, dict):
        return None
    for k in ("export_session", "export_session_id", "session_id"):
        value = obj.get(k)
        if isinstance(value, str) and value.strip():
            return value.strip()
    metadata = obj.get("metadata")
    if isinstance(metadata, dict):
        return _extract_export_session(metadata)
    return None


def _freshness(observed: datetime | None, max_age_hours: int) -> str:
    if observed is None:
        return "UNKNOWN"
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    age = (_utcnow() - observed.astimezone(timezone.utc)).total_seconds() / 3600
    if age < -1:
        return "FUTURE_TIMESTAMP"
    return "FRESH" if age <= max_age_hours else "STALE"


def _manifest(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^([0-9a-fA-F]{64})\s+[* ]?(.+)$", line)
            if match:
                out[Path(match.group(2).strip()).name] = match.group(1).lower()
    except OSError:
        return {}
    return out


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="strict"))


def _load_jsonl(path: Path) -> list[Any]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _safe_rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        return path.name


class ScoreEngine:
    """Read score artifacts and maintain a sanitized comparison baseline.

    External sources are never modified. The only write is the optional baseline JSON.
    """

    def __init__(self, roots: dict[str, Path], baseline_path: Path, max_age_hours: int = 168):
        self.roots = {k: v.expanduser() for k, v in roots.items() if str(v)}
        self.baseline_path = baseline_path.expanduser()
        self.max_age_hours = max_age_hours

    def source_status(self) -> dict[str, Any]:
        return {
            name: {"path": str(path), "available": path.exists(), "is_dir": path.is_dir() if path.exists() else False}
            for name, path in self.roots.items()
        }

    def _files_for(self, source: str, root: Path) -> list[Path]:
        if not root.exists():
            return []
        files: set[Path] = set()
        if source == "ugreen-server":
            for rel in (
                "scores/latest.json", "scores/deepdiag.json", "scores/history.jsonl",
                "scores/network-score.json", "scores/export-session.json", "dashboard/autocheck.json",
            ):
                p = root / rel
                if p.is_file():
                    files.add(p)
            scores = root / "scores"
            if scores.is_dir():
                files.update(scores.glob("*-score.json"))
                files.update(scores.glob("*diag*.json"))
        elif source == "openjarvis-main":
            for ext in ("*.json", "*.jsonl", "*.md"):
                for p in root.rglob(ext):
                    lower = str(p).lower()
                    if any(k in lower for k in (
                        "score", "health", "stability", "autonomy", "confidence", "learning",
                        "recovery", "failover", "a1", "a3", "network", "nas", "agent", "device"
                    )):
                        files.add(p)
        elif source == "drive-datahub":
            for sub in ("02_Index", "03_Projekte", "06_Logs_Exports"):
                base = root / sub
                if not base.exists():
                    continue
                for ext in ("*.json", "*.jsonl", "*.md"):
                    files.update(base.rglob(ext))
        return sorted(files)

    def _baseline(self) -> dict[str, Any]:
        if not self.baseline_path.exists():
            return {"records": {}}
        try:
            data = json.loads(self.baseline_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("records"), dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return {"records": {}}

    def _scan_file(self, source: str, root: Path, path: Path, previous: dict[str, Any]) -> list[ScoreRecord]:
        rel = _safe_rel(path, root)
        file_hash = _sha256(path)
        manifest = _manifest(path.parent / "manifest.sha256")
        expected = manifest.get(path.name)
        hash_verified: bool | None = None if expected is None else expected == file_hash

        try:
            if path.suffix == ".jsonl":
                docs = _load_jsonl(path)
            elif path.suffix == ".json":
                docs = [_load_json(path)]
            elif path.suffix == ".md":
                docs = [self._markdown_doc(path)]
            else:
                return []
        except (OSError, json.JSONDecodeError, UnicodeError):
            return []

        records: list[ScoreRecord] = []
        for idx, doc in enumerate(docs):
            if not isinstance(doc, dict):
                continue
            observed = _extract_times(doc)
            commit = _extract_commit(doc)
            export_session = _extract_export_session(doc)
            evidence = _classify(path, doc, hash_verified, export_session)
            freshness = _freshness(observed, self.max_age_hours)
            provenance_verified = bool(
                evidence == "REAL_DEVICE_MEASUREMENT" or
                (evidence == "VERIFIED_EXPORT" and hash_verified is True and export_session)
            )

            for metric, raw in _iter_scalars(doc):
                if not _candidate_metric(metric, raw):
                    continue
                value = _number(raw)
                status = None if value is not None else _normalize_status(raw)
                key = f"{source}|{rel}|{idx}|{metric}"
                old = previous.get(key, {}) if isinstance(previous.get(key), dict) else {}
                prev_value = _number(old.get("value"))
                prev_status = _normalize_status(old.get("status"))
                new = key not in previous
                changed = bool(
                    new
                    or value != prev_value
                    or status != prev_status
                    or evidence != old.get("evidence_class")
                    or provenance_verified != bool(old.get("provenance_verified", False))
                    or hash_verified != old.get("hash_verified")
                    or export_session != old.get("export_session")
                    or file_hash != old.get("file_sha256")
                )
                delta = value - prev_value if value is not None and prev_value is not None else None
                warning = self._warning(metric, value, status, prev_value, prev_status, evidence, freshness, hash_verified)
                countable = bool(
                    changed
                    and evidence in REAL_EVIDENCE
                    and provenance_verified
                    and freshness == "FRESH"
                    and hash_verified is not False
                    and delta is not None
                    and delta > 0
                )
                records.append(ScoreRecord(
                    key=key, name=metric, value=value, status=status, source=source, path=rel,
                    observed_at=_iso(observed), file_sha256=file_hash, git_commit=commit,
                    export_session=export_session, evidence_class=evidence,
                    provenance_verified=provenance_verified, hash_verified=hash_verified,
                    freshness=freshness, previous_value=prev_value, previous_status=prev_status,
                    delta=delta, changed=changed, new=new, counts_as_real_improvement=countable,
                    warning=warning,
                ))
        return records

    @staticmethod
    def _markdown_doc(path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        data: dict[str, Any] = {
            "evidence_class": "ARCHIVE_CLAIM" if "readme" in path.name.lower() else "UNKNOWN"
        }
        time_match = re.search(
            r"20\d{2}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?",
            text,
        )
        if time_match:
            data["timestamp"] = time_match.group(0)
        for match in re.finditer(
            r"(?im)\b([A-Za-z0-9_./ -]{2,48}?(?:score|health|stability|autonomy|confidence|learning|recovery|failover|A1|A3))\b\s*[:=\-]\s*(\d+(?:\.\d+)?|PASS|FAIL|BLOCKED|NOT_RUN|TRUE|FALSE)",
            text,
        ):
            name = re.sub(r"\s+", "_", match.group(1).strip().lower())
            raw = match.group(2)
            try:
                value: Any = float(raw)
            except ValueError:
                value = raw
            data[name] = value
        return data

    @staticmethod
    def _warning(
        metric: str, value: float | None, status: str | None, prev_value: float | None,
        prev_status: str | None, evidence: str, freshness: str, hash_verified: bool | None
    ) -> str | None:
        notes: list[str] = []
        if hash_verified is False:
            notes.append("HASH_MISMATCH")
        if freshness in {"STALE", "FUTURE_TIMESTAMP"}:
            notes.append(freshness)
        if value is not None and prev_value is not None:
            if value < prev_value:
                notes.append("NUMERIC_REGRESSION")
            elif abs(value - prev_value) >= 25:
                notes.append("UNEXPECTED_JUMP")
        if status is not None and prev_status is not None and status != prev_status:
            if any(word in status.lower() for word in RISK_WORDS):
                notes.append("STATUS_REGRESSION")
            else:
                notes.append("STATUS_CHANGE")
        if evidence not in REAL_EVIDENCE:
            notes.append("NON_REAL_EVIDENCE")
        if any(k in metric.lower() for k in ("a1", "a3", "health", "recovery", "failover")) and status:
            if any(word in status.lower() for word in RISK_WORDS):
                notes.append("GATE_OR_HEALTH_ALERT")
        return ",".join(dict.fromkeys(notes)) or None

    @staticmethod
    def _metric_identity(name: str) -> str:
        leaf = name.split(".")[-1]
        return re.sub(r"\\[\\d+\\]", "", leaf).lower()

    def scan(self, include_unchanged: bool = False) -> dict[str, Any]:
        baseline = self._baseline()
        previous = baseline.get("records", {})
        all_records: list[ScoreRecord] = []
        files_scanned = 0

        for source, root in self.roots.items():
            for path in self._files_for(source, root):
                files_scanned += 1
                all_records.extend(self._scan_file(source, root, path, previous))

        records = all_records if include_unchanged else [r for r in all_records if r.changed]
        alerts = [
            r for r in records
            if r.warning
            and any(x in r.warning for x in (
                "REGRESSION", "UNEXPECTED_JUMP", "HASH_MISMATCH",
                "GATE_OR_HEALTH_ALERT", "FUTURE_TIMESTAMP"
            ))
            and (r.evidence_class in REAL_EVIDENCE or r.hash_verified is False)
        ]
        real_verified_changes = [
            r for r in records
            if r.evidence_class in REAL_EVIDENCE
            and r.provenance_verified
            and r.freshness == "FRESH"
            and r.hash_verified is not False
        ]
        real_improvements = [r for r in records if r.counts_as_real_improvement]
        first_real_device_values = [
            r for r in records
            if r.evidence_class == "REAL_DEVICE_MEASUREMENT"
            and r.provenance_verified
            and r.freshness == "FRESH"
            and r.hash_verified is not False
            and previous.get(r.key, {}).get("evidence_class") != "REAL_DEVICE_MEASUREMENT"
        ]

        current_verified = [
            r for r in all_records
            if r.evidence_class in REAL_EVIDENCE
            and r.provenance_verified
            and r.freshness == "FRESH"
            and r.hash_verified is not False
        ]
        by_metric: dict[str, list[ScoreRecord]] = {}
        for record in current_verified:
            by_metric.setdefault(self._metric_identity(record.name), []).append(record)

        conflicts: list[dict[str, Any]] = []
        for name, group in by_metric.items():
            signatures = {
                ("value", r.value) if r.value is not None else ("status", r.status)
                for r in group
            }
            if len(group) > 1 and len(signatures) > 1:
                conflicts.append({
                    "name": name,
                    "records": [
                        {
                            "source": r.source,
                            "path": r.path,
                            "value": r.value,
                            "status": r.status,
                            "evidence_class": r.evidence_class,
                            "observed_at": r.observed_at,
                            "file_sha256": r.file_sha256,
                        }
                        for r in group
                    ],
                })

        changed_keys = {r.key for r in records}
        new_or_changed_conflicts = [
            conflict for conflict in conflicts
            if any(
                record.key in changed_keys
                for record in current_verified
                if self._metric_identity(record.name) == conflict["name"]
            )
        ]

        current_keys = {r.key for r in all_records}
        available_sources = {name for name, root in self.roots.items() if root.exists()}
        missing_verified_records = [
            {
                "key": key,
                **old,
            }
            for key, old in previous.items()
            if isinstance(old, dict)
            and old.get("source") in available_sources
            and key not in current_keys
            and old.get("evidence_class") in REAL_EVIDENCE
            and bool(old.get("provenance_verified"))
        ]

        return {
            "generated_at": _iso(_utcnow()),
            "baseline_generated_at": baseline.get("generated_at"),
            "source_status": self.source_status(),
            "files_scanned": files_scanned,
            "changed_count": len(records),
            "real_verified_change_count": len(real_verified_changes),
            "real_improvement_count": len(real_improvements),
            "alert_count": len(alerts),
            "conflict_count": len(conflicts),
            "new_or_changed_conflict_count": len(new_or_changed_conflicts),
            "first_real_device_value_count": len(first_real_device_values),
            "missing_verified_record_count": len(missing_verified_records),
            "records": [asdict(r) for r in records],
            "alerts": [asdict(r) for r in alerts],
            "conflicts": conflicts,
            "new_or_changed_conflicts": new_or_changed_conflicts,
            "first_real_device_values": [asdict(r) for r in first_real_device_values],
            "missing_verified_records": missing_verified_records,
            "notification_recommended": bool(
                real_verified_changes or alerts or new_or_changed_conflicts or missing_verified_records
            ),
        }

    def commit_baseline(self) -> dict[str, Any]:
        full = self.scan(include_unchanged=True)
        records = {
            r["key"]: {
                "value": r["value"],
                "status": r["status"],
                "source": r["source"],
                "path": r["path"],
                "observed_at": r["observed_at"],
                "file_sha256": r["file_sha256"],
                "git_commit": r["git_commit"],
                "export_session": r["export_session"],
                "evidence_class": r["evidence_class"],
                "provenance_verified": r["provenance_verified"],
                "hash_verified": r["hash_verified"],
            }
            for r in full["records"]
        }
        payload = {"schema_version": 1, "generated_at": full["generated_at"], "records": records}
        self.baseline_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.baseline_path.with_suffix(self.baseline_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.baseline_path)
        return {
            "baseline_path": str(self.baseline_path),
            "generated_at": payload["generated_at"],
            "records": len(records),
        }


def engine_from_env() -> ScoreEngine:
    roots = {
        "ugreen-server": Path(os.environ.get("AEGIS_UGREEN_REPO", "/opt/aegis/Ugreen-server")),
        "openjarvis-main": Path(os.environ.get("AEGIS_OPENJARVIS_REPO", "/opt/aegis/OpenJarvis-main")),
        "drive-datahub": Path(os.environ.get(
            "AEGIS_DRIVE_HUB", "/mnt/google-drive/00_KI-Agenten_Datenhub"
        )),
    }
    state_dir = Path(os.environ.get("AEGIS_SCORE_STATE_DIR", "/var/lib/aegis-score-mcp"))
    max_age = int(os.environ.get("AEGIS_SCORE_FRESHNESS_HOURS", "168"))
    return ScoreEngine(
        roots=roots,
        baseline_path=state_dir / "baseline.json",
        max_age_hours=max_age,
    )
