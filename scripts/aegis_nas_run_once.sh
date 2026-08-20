#!/usr/bin/env bash
set -Eeuo pipefail

# Guarded one-shot runner for the UGREEN AEGIS export path.
# Default: local-only. It detects/exports real AEGIS data, verifies it, runs
# the local autocheck, and performs no remote write. --commit and --push are
# explicit opt-ins; --push implies --commit.

DO_COMMIT=0
DO_PUSH=0
REPO_ROOT="$(pwd)"

usage() {
  cat <<'EOF'
Usage: scripts/aegis_nas_run_once.sh [--repo-root PATH] [--commit] [--push]

Default behavior is local-only and performs:
  source detect -> atomic export -> schema/session/hash verification -> local autocheck

--commit     Commit only generated AEGIS artifacts locally.
--push       Commit if needed and push the current branch (explicit remote write).
--repo-root  Repository root (default: current directory).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root) REPO_ROOT="${2:?missing path}"; shift 2 ;;
    --commit) DO_COMMIT=1; shift ;;
    --push) DO_COMMIT=1; DO_PUSH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 64 ;;
  esac
done

REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"
cd "$REPO_ROOT"

[[ -d .git ]] || { echo "AEGIS_RUN status=error reason=not_git_repo" >&2; exit 2; }
[[ -f scripts/aegis_source_detect.sh ]] || { echo "AEGIS_RUN status=error reason=missing_source_detector" >&2; exit 2; }
[[ -f scripts/aegis_export_collect.sh ]] || { echo "AEGIS_RUN status=error reason=missing_exporter" >&2; exit 2; }
[[ -f scripts/aegis_repo_autocheck.py ]] || { echo "AEGIS_RUN status=error reason=missing_autocheck" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "AEGIS_RUN status=error reason=python3_missing" >&2; exit 2; }

# Exact files this runner is allowed to create/update/delete/commit.
ARTIFACTS=(
  scores/latest.json
  scores/deepdiag.json
  scores/history.jsonl
  scores/export-session.json
  scores/manifest.sha256
  dashboard/index.html
  dashboard/status.json
  dashboard/autocheck.json
  fixes/done/fix-package-002.json
)

# Fail closed if any pre-existing change is outside the generated artifact set.
python3 - "${ARTIFACTS[@]}" <<'PY'
import subprocess, sys
allowed = set(sys.argv[1:])
out = subprocess.check_output(
    ['git', 'status', '--porcelain=v1', '--untracked-files=all'],
    text=True,
)
bad = []
for line in out.splitlines():
    if not line:
        continue
    path = line[3:]
    if ' -> ' in path:
        path = path.split(' -> ', 1)[1]
    if path not in allowed:
        bad.append(line)
if bad:
    print('AEGIS_RUN status=blocked reason=unrelated_dirty_worktree', file=sys.stderr)
    print('\n'.join(bad), file=sys.stderr)
    raise SystemExit(10)
PY

echo "AEGIS_RUN phase=detect_export"
bash scripts/aegis_source_detect.sh --export --repo-root "$REPO_ROOT"

[[ -f scores/latest.json ]] || { echo "AEGIS_RUN status=error reason=latest_missing_after_export" >&2; exit 11; }
[[ -f scores/export-session.json ]] || { echo "AEGIS_RUN status=error reason=export_session_missing" >&2; exit 11; }
[[ -s scores/manifest.sha256 ]] || { echo "AEGIS_RUN status=error reason=manifest_missing_or_empty" >&2; exit 11; }

echo "AEGIS_RUN phase=validate_schema"
python3 - <<'PY'
import json
from pathlib import Path
p = Path('scores/latest.json')
d = json.loads(p.read_text(encoding='utf-8'))
required = ('generated_at', 'network_score', 'devices')
missing = [k for k in required if k not in d]
if missing:
    raise SystemExit(f'missing required fields: {missing}')
score = d['network_score']
if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= float(score) <= 100:
    raise SystemExit('network_score must be numeric 0..100')
if not isinstance(d['devices'], list):
    raise SystemExit('devices must be a list')
for i, dev in enumerate(d['devices']):
    if not isinstance(dev, dict):
        raise SystemExit(f'devices[{i}] must be an object')
    if 'id' not in dev or 'score' not in dev:
        raise SystemExit(f'devices[{i}] missing id/score')
    s = dev['score']
    if isinstance(s, bool) or not isinstance(s, (int, float)) or not 0 <= float(s) <= 100:
        raise SystemExit(f'devices[{i}].score must be numeric 0..100')
print('AEGIS schema baseline validation OK')
PY

echo "AEGIS_RUN phase=verify_hashes"
if command -v sha256sum >/dev/null 2>&1; then
  (cd scores && sha256sum -c manifest.sha256)
elif command -v shasum >/dev/null 2>&1; then
  (cd scores && shasum -a 256 -c manifest.sha256)
else
  echo "AEGIS_RUN status=error reason=no_sha256_utility" >&2
  exit 4
fi

echo "AEGIS_RUN phase=autocheck"
python3 scripts/aegis_repo_autocheck.py

echo "AEGIS_RUN phase=summary"
python3 - <<'PY'
import json
from pathlib import Path
latest = json.loads(Path('scores/latest.json').read_text(encoding='utf-8'))
auto = Path('dashboard/autocheck.json')
report = json.loads(auto.read_text(encoding='utf-8')) if auto.exists() else {}
print(f"evidence_verified={report.get('evidence_verified', False)}")
print(f"network_score={latest.get('network_score')}")
print(f"device_count={len(latest.get('devices', []))}")
print(f"deepdiag_score={report.get('deepdiag_score', 'unavailable')}")
print(f"priority_findings={len(report.get('priority_findings', []))}")
print(f"recommended_next_step={report.get('recommended_next_step', 'unavailable')}")
PY

if [[ "$DO_COMMIT" -eq 0 ]]; then
  echo "AEGIS_RUN status=ready_local_only"
  echo "Review: git diff -- ${ARTIFACTS[*]}"
  echo "To commit locally: bash scripts/aegis_nas_run_once.sh --commit"
  echo "To commit and push: bash scripts/aegis_nas_run_once.sh --push"
  exit 0
fi

# Stage only allowlisted generated paths, including deletions of stale artifacts.
for path in "${ARTIFACTS[@]}"; do
  if [[ -e "$path" ]] || git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
    git add -A -- "$path"
  fi
done

if git diff --cached --quiet; then
  echo "AEGIS_RUN status=no_changes"
else
  git commit -m "AEGIS: export real NAS diagnostics"
  echo "AEGIS_RUN status=committed"
fi

if [[ "$DO_PUSH" -eq 1 ]]; then
  branch="$(git branch --show-current)"
  [[ -n "$branch" ]] || { echo "AEGIS_RUN status=blocked reason=detached_head" >&2; exit 12; }
  git push origin "$branch"
  echo "AEGIS_RUN status=pushed branch=$branch"
fi
