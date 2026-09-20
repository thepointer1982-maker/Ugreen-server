#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-auto}"
shift || true
TASK="${*:-}"
[[ -n "$TASK" ]] || { echo "Usage: aegis_coder_router.sh auto|local|codex <task>" >&2; exit 2; }

REPO_ROOT="${AEGIS_CODER_REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
STATE_ROOT="${AEGIS_CODER_STATE_ROOT:-$HOME/.local/state/aegis-coder}"
WORK_ROOT="${AEGIS_CODER_WORK_ROOT:-$HOME/.local/share/aegis-coder/worktrees}"
LOCAL_MODEL="${AEGIS_LOCAL_CODER_MODEL:-qwen2.5-coder:7b}"
CONFIG="$REPO_ROOT/config/opencode/aegis-local.json"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"
WORKTREE="$WORK_ROOT/$STAMP"
REPORT="$STATE_ROOT/$STAMP"

mkdir -p "$WORK_ROOT" "$REPORT"
chmod 700 "$STATE_ROOT" "$WORK_ROOT" 2>/dev/null || true

git -C "$REPO_ROOT" diff --quiet || { echo "blocked: tracked working tree changes present" >&2; exit 4; }
git -C "$REPO_ROOT" worktree add --detach "$WORKTREE" HEAD >/dev/null
cleanup() {
  if [[ "${AEGIS_CODER_KEEP_WORKTREE:-0}" != "1" ]]; then
    git -C "$REPO_ROOT" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

local_ready() {
  command -v opencode >/dev/null 2>&1 || return 1
  curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || return 1
  [[ -f "$CONFIG" ]] || return 1
}

codex_ready() {
  command -v codex >/dev/null 2>&1
}

case "$MODE" in
  auto)
    if local_ready; then MODE="local"; elif codex_ready; then MODE="codex"; else echo "blocked: neither local OpenCode/Ollama nor Codex CLI is ready" >&2; exit 5; fi
    ;;
  local) local_ready || { echo "blocked: OpenCode/Ollama local stack not ready" >&2; exit 5; } ;;
  codex) codex_ready || { echo "blocked: Codex CLI not installed" >&2; exit 5; } ;;
  *) echo "invalid mode: $MODE" >&2; exit 2 ;;
esac

echo "mode=$MODE" | tee "$REPORT/meta.txt"
echo "worktree=$WORKTREE" | tee -a "$REPORT/meta.txt"

set +e
if [[ "$MODE" == "local" ]]; then
  (
    cd "$WORKTREE"
    export OPENCODE_CONFIG="$CONFIG"
    export OPENCODE_DISABLE_AUTOUPDATE=1
    export OPENCODE_AUTO_SHARE=false
    opencode run --dir "$WORKTREE" --model "ollama/$LOCAL_MODEL" --agent build "$TASK"
  ) >"$REPORT/agent.stdout" 2>"$REPORT/agent.stderr"
  rc=$?
else
  (
    cd "$WORKTREE"
    unset OPENAI_API_KEY
    unset OPENAI_ORG_ID
    codex exec --ephemeral --sandbox workspace-write "$TASK"
  ) >"$REPORT/agent.stdout" 2>"$REPORT/agent.stderr"
  rc=$?
fi
set -e

git -C "$WORKTREE" diff --binary > "$REPORT/changes.patch" || true
git -C "$WORKTREE" status --short > "$REPORT/status.txt" || true

validate_rc=0
git -C "$WORKTREE" diff --check >>"$REPORT/validation.txt" 2>&1 || validate_rc=$?
if [[ -d "$WORKTREE/scripts" ]]; then
  python3 -m compileall -q "$WORKTREE/scripts" >>"$REPORT/validation.txt" 2>&1 || validate_rc=$?
  while IFS= read -r -d "" file; do
    bash -n "$file" >>"$REPORT/validation.txt" 2>&1 || validate_rc=$?
  done < <(find "$WORKTREE/scripts" -maxdepth 1 -type f -name "*.sh" -print0)
fi

cat > "$REPORT/result.json" <<EOF
{"mode":"$MODE","agent_rc":$rc,"validation_rc":$validate_rc,"report":"$REPORT","worktree_kept":${AEGIS_CODER_KEEP_WORKTREE:-0}}
EOF
cat "$REPORT/result.json"

if [[ "$rc" -ne 0 || "$validate_rc" -ne 0 ]]; then exit 20; fi
exit 0
