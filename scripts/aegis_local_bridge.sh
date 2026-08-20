#!/usr/bin/env bash
set -Eeuo pipefail

# Local AEGIS bridge for UGREEN. No remote command channel is opened.
# It runs the existing guarded one-shot pipeline on a schedule or on demand.
# Git push is disabled unless AEGIS_BRIDGE_PUSH=1 is explicitly configured locally.

REPO_ROOT="${AEGIS_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOCK_FILE="${AEGIS_BRIDGE_LOCK_FILE:-$REPO_ROOT/.aegis-bridge.lock}"
LOG_DIR="${AEGIS_BRIDGE_LOG_DIR:-$REPO_ROOT/logs}"
PUSH="${AEGIS_BRIDGE_PUSH:-0}"

mkdir -p "$LOG_DIR"

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  flock -n 9 || { echo "AEGIS_BRIDGE status=skip reason=already_running"; exit 0; }
else
  LOCK_DIR="${LOCK_FILE}.d"
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "AEGIS_BRIDGE status=skip reason=already_running"
    exit 0
  fi
  trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
fi

cd "$REPO_ROOT"
[[ -d .git ]] || { echo "AEGIS_BRIDGE status=error reason=not_git_repo" >&2; exit 2; }
[[ -f scripts/aegis_nas_run_once.sh ]] || { echo "AEGIS_BRIDGE status=error reason=runner_missing" >&2; exit 2; }

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/bridge-$STAMP.log"

{
  echo "AEGIS_BRIDGE started_at=$STAMP"
  echo "AEGIS_BRIDGE repo_root=$REPO_ROOT"
  echo "AEGIS_BRIDGE push_enabled=$PUSH"

  # Refuse to auto-update when unrelated local changes exist. The guarded runner
  # performs an additional allowlist check before exporting.
  if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
    allowed_only="$(git status --porcelain --untracked-files=normal | awk '{print substr($0,4)}' | grep -Ev '^(scores/|dashboard/|fixes/done/fix-package-002.json$)' || true)"
    if [[ -n "$allowed_only" ]]; then
      echo "AEGIS_BRIDGE status=blocked reason=unrelated_dirty_worktree" >&2
      printf '%s\n' "$allowed_only" >&2
      exit 10
    fi
  fi

  # Fast-forward only; never rewrite local history.
  git fetch origin main --quiet
  git checkout main --quiet
  git merge --ff-only origin/main --quiet

  if [[ "$PUSH" == "1" ]]; then
    bash scripts/aegis_nas_run_once.sh --push
  else
    bash scripts/aegis_nas_run_once.sh
  fi

  echo "AEGIS_BRIDGE status=ok"
} 2>&1 | tee "$LOG_FILE"
