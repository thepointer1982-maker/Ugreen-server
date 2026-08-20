#!/usr/bin/env bash
set -Eeuo pipefail

# Guarded one-shot runner for the UGREEN AEGIS export path.
# Default: local-only. It detects/exports real AEGIS data, verifies it, runs
# the local autocheck, and stages nothing remotely. --commit and --push are
# explicit opt-ins; --push implies --commit.

DO_COMMIT=0
DO_PUSH=0
REPO_ROOT="$(pwd)"

usage() {
  cat <<'EOF'
Usage: scripts/aegis_nas_run_once.sh [--repo-root PATH] [--commit] [--push]

Default behavior is local-only and performs:
  source detect -> export -> schema/hash verification -> local autocheck

--commit     Commit exported score/dashboard/autocheck/fix artifacts locally.
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

# Do not mix a real export with unrelated local edits.
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "AEGIS_RUN status=blocked reason=dirty_worktree" >&2
  git status --short >&2
  exit 10
fi

echo "AEGIS_RUN phase=detect_export"
bash scripts/aegis_source_detect.sh --export --repo-root "$REPO_ROOT"

[[ -f scores/latest.json ]] || { echo "AEGIS_RUN status=error reason=latest_missing_after_export" >&2; exit 11; }

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

if [[ -s scores/manifest.sha256 ]]; then
  echo "AEGIS_RUN phase=verify_hashes"
  if command -v sha256sum >/dev/null 2>&1; then
    (cd scores && sha256sum -c manifest.sha256)
  else
    (cd scores && shasum -a 256 -c manifest.sha256)
  fi
fi

echo "AEGIS_RUN phase=autocheck"
python3 scripts/aegis_repo_autocheck.py

# Restrict commit scope to generated AEGIS artifacts only.
ARTIFACTS=(scores dashboard fixes/done/fix-package-002.json)

echo "AEGIS_RUN phase=summary"
python3 - <<'PY'
import json
from pathlib import Path
latest = json.loads(Path('scores/latest.json').read_text(encoding='utf-8'))
auto = Path('dashboard/autocheck.json')
report = json.loads(auto.read_text(encoding='utf-8')) if auto.exists() else {}
print(f"network_score={latest.get('network_score')}")
print(f"device_count={len(latest.get('devices', []))}")
print(f"deepdiag_score={report.get('deepdiag_score', 'unavailable')}")
print(f"priority_findings={len(report.get('priority_findings', []))}")
print(f"recommended_next_step={report.get('recommended_next_step', 'unavailable')}")
PY

if [[ "$DO_COMMIT" -eq 0 ]]; then
  echo "AEGIS_RUN status=ready_local_only"
  echo "Review: git diff -- scores dashboard fixes/done/fix-package-002.json"
  echo "To commit locally: bash scripts/aegis_nas_run_once.sh --commit"
  echo "To commit and push: bash scripts/aegis_nas_run_once.sh --push"
  exit 0
fi

git add -- "${ARTIFACTS[@]}"
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
