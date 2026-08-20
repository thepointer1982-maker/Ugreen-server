#!/usr/bin/env bash
set -Eeuo pipefail

if [[ -n "${AEGIS_SOURCE_ROOT:-}" ]]; then
  SOURCE_ROOT="$AEGIS_SOURCE_ROOT"
elif [[ "$(uname -s)" == "Darwin" ]]; then
  SOURCE_ROOT="$HOME/Library/Application Support/AEGIS-Device-AI"
else
  SOURCE_ROOT="/var/lib/aegis-device-ai"
fi

REPO_ROOT="${1:-$(pwd)}"
REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"
SCORES_DIR="$REPO_ROOT/scores"
DASH_DIR="$REPO_ROOT/dashboard"
mkdir -p "$SCORES_DIR" "$DASH_DIR"
command -v python3 >/dev/null 2>&1 || { echo "AEGIS export aborted: python3 missing" >&2; exit 4; }

LATEST_SRC="$SOURCE_ROOT/reports/latest.json"
[[ -f "$LATEST_SRC" ]] || { echo "AEGIS export aborted: reports/latest.json missing" >&2; exit 3; }

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/aegis-export.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/scores" "$STAGE/dashboard"

copy_if_present() {
  local src="$1" dst="$2"
  if [[ -f "$src" ]]; then
    cp -- "$src" "$dst"
  fi
}

copy_if_present "$LATEST_SRC" "$STAGE/scores/latest.json"
copy_if_present "$SOURCE_ROOT/reports/deepdiag.json" "$STAGE/scores/deepdiag.json"
copy_if_present "$SOURCE_ROOT/data/history.jsonl" "$STAGE/scores/history.jsonl"
copy_if_present "$SOURCE_ROOT/reports/dashboard.html" "$STAGE/dashboard/index.html"
copy_if_present "$LATEST_SRC" "$STAGE/dashboard/status.json"

# Fail closed on mixed evidence. Deepdiag must correlate with latest by a shared
# run/session identifier, or by timestamps no more than 15 minutes apart.
python3 - "$STAGE/scores/latest.json" "$STAGE/scores/deepdiag.json" "$STAGE/scores/export-session.json" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path

latest_p, deep_p, session_p = map(Path, sys.argv[1:])
latest = json.loads(latest_p.read_text(encoding='utf-8'))
required = ('generated_at', 'network_score', 'devices')
if any(k not in latest for k in required):
    raise SystemExit('latest.json missing required fields')
score = latest['network_score']
if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= float(score) <= 100:
    raise SystemExit('latest.json network_score invalid')
if not isinstance(latest['devices'], list):
    raise SystemExit('latest.json devices invalid')

def parse_ts(v):
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip().replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

latest_ts = parse_ts(latest.get('generated_at'))
if latest_ts is None:
    raise SystemExit('latest.json generated_at invalid')

correlation = 'latest_only'
deep = None
if deep_p.exists():
    deep = json.loads(deep_p.read_text(encoding='utf-8'))
    if not isinstance(deep, dict):
        raise SystemExit('deepdiag.json must be an object')
    id_keys = ('run_id', 'session_id', 'probe_session_id', 'evidence_id')
    matched_id = False
    conflicting_id = False
    for key in id_keys:
        lv, dv = latest.get(key), deep.get(key)
        if lv is not None and dv is not None:
            if str(lv) == str(dv):
                matched_id = True
            else:
                conflicting_id = True
    if conflicting_id:
        raise SystemExit('mixed evidence: latest/deepdiag identifiers conflict')
    if matched_id:
        correlation = 'shared_identifier'
    else:
        deep_ts = parse_ts(deep.get('generated_at'))
        if deep_ts is None:
            raise SystemExit('mixed evidence cannot be excluded: deepdiag lacks correlatable id/timestamp')
        delta = abs((latest_ts - deep_ts).total_seconds())
        if delta > 900:
            raise SystemExit(f'mixed evidence: latest/deepdiag timestamps differ by {delta:.0f}s')
        correlation = 'timestamp_window_900s'

files = ['latest.json']
for name in ('deepdiag.json', 'history.jsonl'):
    if (latest_p.parent / name).exists():
        files.append(name)
session = {
    'schema_version': 1,
    'exported_at': datetime.now(timezone.utc).isoformat(),
    'source_generated_at': latest.get('generated_at'),
    'correlation': correlation,
    'files': files,
}
session_p.write_text(json.dumps(session, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
PY

# Refuse obvious secret material in the staged export before touching repo data.
if grep -RIEq '(BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|ghp_[A-Za-z0-9]+|github_pat_|sk-[A-Za-z0-9]|password[[:space:]]*[:=]|token[[:space:]]*[:=])' "$STAGE/scores" "$STAGE/dashboard" 2>/dev/null; then
  echo "AEGIS export aborted: possible secret material detected" >&2
  exit 2
fi

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1"
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1"
  else
    echo "AEGIS export aborted: no SHA-256 utility available" >&2
    exit 4
  fi
}

(
  cd "$STAGE/scores"
  : > manifest.sha256
  for f in latest.json deepdiag.json history.jsonl export-session.json; do
    [[ -f "$f" ]] && hash_file "$f" >> manifest.sha256
  done
)

# Only after staging, validation, correlation and secret checks succeed do we
# replace generated artifacts. This prevents partial/stale mixed exports.
for path in \
  "$SCORES_DIR/latest.json" \
  "$SCORES_DIR/deepdiag.json" \
  "$SCORES_DIR/history.jsonl" \
  "$SCORES_DIR/export-session.json" \
  "$SCORES_DIR/manifest.sha256" \
  "$DASH_DIR/index.html" \
  "$DASH_DIR/status.json" \
  "$DASH_DIR/autocheck.json" \
  "$REPO_ROOT/fixes/done/fix-package-002.json"; do
  rm -f -- "$path"
done

for f in latest.json deepdiag.json history.jsonl export-session.json manifest.sha256; do
  [[ -f "$STAGE/scores/$f" ]] && cp -- "$STAGE/scores/$f" "$SCORES_DIR/$f"
done
for f in index.html status.json; do
  [[ -f "$STAGE/dashboard/$f" ]] && cp -- "$STAGE/dashboard/$f" "$DASH_DIR/$f"
done

echo "AEGIS export complete: atomic fresh evidence written to $SCORES_DIR and $DASH_DIR"
