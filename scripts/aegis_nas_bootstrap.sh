#!/usr/bin/env bash
set -Eeuo pipefail

# AEGIS NAS bootstrap/recovery helper.
# Default is read-only except for an optional local state directory under the repo.
# It does not control devices, alter NAS services, install packages, or push to GitHub.

REPO_ROOT="$(pwd)"
DO_RUN=0
DO_COMMIT=0
DO_PUSH=0
STATE_DIR=""

usage() {
  cat <<'EOF'
Usage: scripts/aegis_nas_bootstrap.sh [--repo-root PATH] [--run] [--commit] [--push]

Default: perform preflight only and print exact blockers/remediation.
--run       Run the guarded local AEGIS one-shot export after preflight passes.
--commit    Pass --commit to the one-shot runner (implies --run).
--push      Pass --push to the one-shot runner (implies --run and --commit).
--repo-root Repository root (default: current directory).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root) REPO_ROOT="${2:?missing path}"; shift 2 ;;
    --run) DO_RUN=1; shift ;;
    --commit) DO_RUN=1; DO_COMMIT=1; shift ;;
    --push) DO_RUN=1; DO_COMMIT=1; DO_PUSH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 64 ;;
  esac
done

REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"
STATE_DIR="$REPO_ROOT/.aegis-bootstrap"
REPORT="$STATE_DIR/preflight.json"
mkdir -p "$STATE_DIR"

python3_cmd="$(command -v python3 || true)"
git_cmd="$(command -v git || true)"
bash_cmd="$(command -v bash || true)"
sha_cmd="$(command -v sha256sum || command -v shasum || true)"

repo_ok=0
branch=""
remote=""
if [[ -d "$REPO_ROOT/.git" && -n "$git_cmd" ]]; then
  repo_ok=1
  branch="$(git -C "$REPO_ROOT" branch --show-current 2>/dev/null || true)"
  remote="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)"
fi

required_scripts=(
  scripts/aegis_source_detect.sh
  scripts/aegis_export_collect.sh
  scripts/aegis_repo_autocheck.py
  scripts/aegis_nas_run_once.sh
)
missing_scripts=()
for p in "${required_scripts[@]}"; do
  [[ -f "$REPO_ROOT/$p" ]] || missing_scripts+=("$p")
done

source_status="unknown"
source_root=""
source_output=""
if [[ -n "$bash_cmd" && -f "$REPO_ROOT/scripts/aegis_source_detect.sh" ]]; then
  set +e
  source_output="$(cd "$REPO_ROOT" && bash scripts/aegis_source_detect.sh 2>&1)"
  rc=$?
  set -e
  if [[ $rc -eq 0 ]]; then
    source_status="ready"
    source_root="$(printf '%s\n' "$source_output" | sed -n 's/^source_root=//p' | head -n1)"
  elif [[ $rc -eq 3 ]]; then
    source_status="no_valid_source"
  elif [[ $rc -eq 4 ]]; then
    source_status="invalid_explicit_source"
  elif [[ $rc -eq 5 ]]; then
    source_status="ambiguous"
  else
    source_status="error_$rc"
  fi
fi

write_ok=0
probe="$STATE_DIR/.write-test.$$"
if (umask 077 && : > "$probe") 2>/dev/null; then
  write_ok=1
  rm -f -- "$probe"
fi

origin_expected=0
case "$remote" in
  *thepointer1982-maker/Ugreen-server.git|*thepointer1982-maker/Ugreen-server) origin_expected=1 ;;
esac

python3 - "$REPORT" "$REPO_ROOT" "$repo_ok" "$branch" "$remote" "$origin_expected" "$python3_cmd" "$git_cmd" "$bash_cmd" "$sha_cmd" "$write_ok" "$source_status" "$source_root" "$source_output" "${missing_scripts[*]}" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path
(
    report, repo_root, repo_ok, branch, remote, origin_expected,
    python3_cmd, git_cmd, bash_cmd, sha_cmd, write_ok,
    source_status, source_root, source_output, missing_scripts,
) = sys.argv[1:]
blockers = []
if repo_ok != '1': blockers.append('not_git_repository')
if not python3_cmd: blockers.append('python3_missing')
if not git_cmd: blockers.append('git_missing')
if not bash_cmd: blockers.append('bash_missing')
if not sha_cmd: blockers.append('sha256_utility_missing')
if write_ok != '1': blockers.append('repo_state_not_writable')
if missing_scripts: blockers.append('required_scripts_missing')
if repo_ok == '1' and not branch: blockers.append('detached_head')
if remote and origin_expected != '1': blockers.append('unexpected_origin')
if source_status != 'ready': blockers.append(f'source_{source_status}')
status = 'ready' if not blockers else 'blocked'
data = {
    'schema_version': 1,
    'generated_at': datetime.now(timezone.utc).isoformat(),
    'status': status,
    'repo_root': repo_root,
    'git': {
        'is_repo': repo_ok == '1',
        'branch': branch or None,
        'origin': remote or None,
        'origin_expected': origin_expected == '1' if remote else None,
    },
    'tools': {
        'python3': python3_cmd or None,
        'git': git_cmd or None,
        'bash': bash_cmd or None,
        'sha256': sha_cmd or None,
    },
    'repo_state_writable': write_ok == '1',
    'missing_scripts': [x for x in missing_scripts.split() if x],
    'source': {
        'status': source_status,
        'root': source_root or None,
        'raw': source_output[-4000:],
    },
    'blockers': blockers,
}
Path(report).write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
print(json.dumps(data, ensure_ascii=False))
PY

status="$(python3 - "$REPORT" <<'PY'
import json,sys
print(json.load(open(sys.argv[1], encoding='utf-8'))['status'])
PY
)"

if [[ "$status" != "ready" ]]; then
  echo "AEGIS_BOOTSTRAP status=blocked report=$REPORT" >&2
  echo "Remediation:" >&2
  python3 - "$REPORT" <<'PY'
import json,sys
b=json.load(open(sys.argv[1], encoding='utf-8'))['blockers']
for x in b:
    hints={
      'not_git_repository':'Run this inside the cloned Ugreen-server repository.',
      'python3_missing':'Install/enable Python 3 using the NAS-supported package mechanism.',
      'git_missing':'Install/enable Git using the NAS-supported package mechanism.',
      'bash_missing':'Use a shell environment that provides bash.',
      'sha256_utility_missing':'Provide sha256sum or shasum.',
      'repo_state_not_writable':'Fix permissions for the repository working tree only.',
      'required_scripts_missing':'git pull --ff-only on main, then rerun.',
      'detached_head':'Checkout main or another named branch.',
      'unexpected_origin':'Verify the repository origin before any push.',
      'source_no_valid_source':'Locate AEGIS reports/latest.json or set AEGIS_SOURCE_ROOT explicitly.',
      'source_invalid_explicit_source':'Correct or unset AEGIS_SOURCE_ROOT.',
      'source_ambiguous':'Set AEGIS_SOURCE_ROOT to the intended AEGIS root.',
    }
    print(f"- {x}: {hints.get(x, 'Inspect preflight.json for details.')}")
PY
  exit 20
fi

echo "AEGIS_BOOTSTRAP status=ready report=$REPORT"

if [[ "$DO_RUN" -eq 0 ]]; then
  echo "Next: bash scripts/aegis_nas_bootstrap.sh --run"
  exit 0
fi

args=()
[[ "$DO_COMMIT" -eq 1 ]] && args+=(--commit)
[[ "$DO_PUSH" -eq 1 ]] && args+=(--push)
exec bash "$REPO_ROOT/scripts/aegis_nas_run_once.sh" "${args[@]}"
