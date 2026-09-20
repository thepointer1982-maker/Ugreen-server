#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-auto}"
shift || true
TASK="${*:-}"
[[ -n "$TASK" ]] || {
  echo "Usage: aegis_coder_router.sh auto|codex-local|local|codex <task>" >&2
  exit 2
}

REPO_ROOT="${AEGIS_CODER_REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
STATE_ROOT="${AEGIS_CODER_STATE_ROOT:-$HOME/.local/state/aegis-coder}"
WORK_ROOT="${AEGIS_CODER_WORK_ROOT:-$HOME/.local/share/aegis-coder/worktrees}"
LOCAL_MODEL="${AEGIS_LOCAL_CODER_MODEL:-qwen2.5-coder:7b}"
CONFIG="$REPO_ROOT/config/opencode/aegis-local.json"
BOOT_STATE="${AEGIS_CODER_BOOT_STATE_FILE:-$HOME/.local/state/aegis-coder-boot/status.json}"
CODEX_OSS_STATE="${AEGIS_CODEX_OSS_STATE_FILE:-$HOME/.local/state/aegis-codex-oss/status.json}"
LOCAL_CODEX_WRAPPER="${AEGIS_CODEX_OSS_WRAPPER:-$HOME/.local/bin/aegis-codex-local}"
ALLOW_CLOUD_CODEX="${AEGIS_ALLOW_CLOUD_CODEX:-0}"
PROJECT_CONTEXT_SCRIPT="$REPO_ROOT/scripts/aegis_project_context.py"
PROJECT_CONTEXT_REQUIRED="${AEGIS_PROJECT_CONTEXT_REQUIRED:-1}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"
WORKTREE="$WORK_ROOT/$STAMP"
REPORT="$STATE_ROOT/$STAMP"

mkdir -p "$WORK_ROOT" "$REPORT"
chmod 700 "$STATE_ROOT" "$WORK_ROOT" 2>/dev/null || true

[[ -z "$(git -C "$REPO_ROOT" status --porcelain)" ]] || {
  echo "blocked: working tree is not clean" >&2
  exit 4
}

git -C "$REPO_ROOT" worktree add --detach "$WORKTREE" HEAD >/dev/null
cleanup() {
  if [[ "${AEGIS_CODER_KEEP_WORKTREE:-0}" != "1" ]]; then
    git -C "$REPO_ROOT" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

codex_local_ready() {
  [[ -x "$LOCAL_CODEX_WRAPPER" ]] || return 1
  command -v curl >/dev/null 2>&1 || return 1
  curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || return 1
  [[ -s "$CODEX_OSS_STATE" ]] || return 1
  python3 - "$CODEX_OSS_STATE" <<'PY'
import json, sys
from pathlib import Path
try:
    d = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    ok = (
        d.get("health") == "healthy"
        and d.get("provider") == "ollama"
        and d.get("model_ready") is True
        and d.get("cloud_model_usage") is False
        and d.get("openai_api_key_required") is False
    )
    raise SystemExit(0 if ok else 1)
except Exception:
    raise SystemExit(1)
PY
}

opencode_local_ready() {
  command -v opencode >/dev/null 2>&1 || return 1
  command -v curl >/dev/null 2>&1 || return 1
  curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || return 1
  [[ -f "$CONFIG" ]] || return 1
}

codex_cloud_ready() {
  command -v codex >/dev/null 2>&1 || return 1
  local status rc
  set +e
  if command -v timeout >/dev/null 2>&1; then
    status="$(timeout 15s codex login status 2>&1)"
    rc=$?
  else
    status="$(codex login status 2>&1)"
    rc=$?
  fi
  set -e
  [[ "$rc" -eq 0 ]] || return 1
  grep -Fq "Logged in using ChatGPT" <<<"$status"
}

boot_selected() {
  [[ -s "$BOOT_STATE" ]] || return 1
  python3 - "$BOOT_STATE" <<'PY'
import json, sys
from pathlib import Path
try:
    d = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    selected = d.get("selected")
    health = d.get("health")
    if selected in {"codex-local","local","codex"} and health in {"healthy","degraded"}:
        print(selected)
except Exception:
    pass
PY
}

select_auto() {
  local preferred
  preferred="$(boot_selected || true)"
  case "$preferred" in
    codex-local)
      codex_local_ready && { MODE="codex-local"; return; }
      ;;
    local)
      opencode_local_ready && { MODE="local"; return; }
      ;;
    codex)
      if [[ "$ALLOW_CLOUD_CODEX" == "1" ]] && codex_cloud_ready; then
        MODE="codex"
        return
      fi
      ;;
  esac

  if codex_local_ready; then
    MODE="codex-local"
  elif opencode_local_ready; then
    MODE="local"
  elif [[ "$ALLOW_CLOUD_CODEX" == "1" ]] && codex_cloud_ready; then
    MODE="codex"
  else
    echo "blocked: no local coder backend is ready; cloud Codex fallback is disabled" >&2
    exit 5
  fi
}

case "$MODE" in
  auto)
    select_auto
    ;;
  codex-local)
    codex_local_ready || {
      echo "blocked: local Codex OSS/Ollama backend not ready" >&2
      exit 5
    }
    ;;
  local)
    opencode_local_ready || {
      echo "blocked: OpenCode/Ollama local stack not ready" >&2
      exit 5
    }
    ;;
  codex)
    codex_cloud_ready || {
      echo "blocked: ChatGPT-authenticated Codex CLI not ready" >&2
      exit 5
    }
    ;;
  *)
    echo "invalid mode: $MODE" >&2
    exit 2
    ;;
esac

case "$MODE" in
  codex-local) CONTEXT_CHANNEL="codex-local" ;;
  local) CONTEXT_CHANNEL="opencode-local" ;;
  codex) CONTEXT_CHANNEL="chat" ;;
  *) CONTEXT_CHANNEL="mcp" ;;
esac

context_rc=0
CONTEXT_PACKET=""
if [[ -f "$PROJECT_CONTEXT_SCRIPT" ]]; then
  set +e
  CONTEXT_PACKET="$(AEGIS_REPO_ROOT="$REPO_ROOT" python3 "$PROJECT_CONTEXT_SCRIPT" packet --channel "$CONTEXT_CHANNEL" 2>"$REPORT/project-context.stderr")"
  context_rc=$?
  set -e
else
  context_rc=127
  echo "project context script missing: $PROJECT_CONTEXT_SCRIPT" >"$REPORT/project-context.stderr"
fi

if [[ "$PROJECT_CONTEXT_REQUIRED" == "1" && "$context_rc" -ne 0 ]]; then
  echo "blocked: signed AEGIS project context is unavailable" >&2
  exit 6
fi
if [[ -z "$CONTEXT_PACKET" ]]; then
  CONTEXT_PACKET='{"status":"unavailable"}'
fi
printf '%s\n' "$CONTEXT_PACKET" > "$REPORT/project-context.json"

FULL_TASK="$(python3 - "$REPORT/project-context.json" "$TASK" <<'PY'
import json, sys
from pathlib import Path
packet = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
task = sys.argv[2]
print(
    "AEGIS AUTHORITATIVE PROJECT CONTEXT\n"
    "Preserve project_id, thread_id, style contract, hard constraints, decisions, "
    "blockers, and pending action across this task. Do not invent real hardware "
    "state. Continue the existing project rather than starting a new project.\n"
    + json.dumps(packet, ensure_ascii=False, sort_keys=True)
    + "\n\nUSER TASK\n"
    + task
)
PY
)"

echo "mode=$MODE" | tee "$REPORT/meta.txt"
echo "context_channel=$CONTEXT_CHANNEL" | tee -a "$REPORT/meta.txt"
echo "context_required=$PROJECT_CONTEXT_REQUIRED" | tee -a "$REPORT/meta.txt"
echo "cloud_fallback_allowed=$ALLOW_CLOUD_CODEX" | tee -a "$REPORT/meta.txt"
echo "boot_state=$BOOT_STATE" | tee -a "$REPORT/meta.txt"
echo "worktree=$WORKTREE" | tee -a "$REPORT/meta.txt"

set +e
case "$MODE" in
  codex-local)
    (
      cd "$WORKTREE"
      unset OPENAI_API_KEY OPENAI_ORG_ID OPENAI_PROJECT_ID OPENAI_BASE_URL CODEX_API_KEY CODEX_ACCESS_TOKEN
      export LC_ALL=C
      "$LOCAL_CODEX_WRAPPER" exec "$FULL_TASK"
    ) >"$REPORT/agent.stdout" 2>"$REPORT/agent.stderr"
    rc=$?
    ;;
  local)
    (
      cd "$WORKTREE"
      export OPENCODE_CONFIG="$CONFIG"
      export OPENCODE_DISABLE_AUTOUPDATE=1
      export OPENCODE_AUTO_SHARE=false
      export OPENCODE_DISABLE_MODELS_FETCH=1
      export OPENCODE_DISABLE_DEFAULT_PLUGINS=1
      export OPENCODE_DISABLE_LSP_DOWNLOAD=1
      opencode run --dir "$WORKTREE" --model "ollama/$LOCAL_MODEL" --agent build "$FULL_TASK"
    ) >"$REPORT/agent.stdout" 2>"$REPORT/agent.stderr"
    rc=$?
    ;;
  codex)
    (
      cd "$WORKTREE"
      unset OPENAI_API_KEY OPENAI_ORG_ID OPENAI_PROJECT_ID OPENAI_BASE_URL CODEX_API_KEY
      export LC_ALL=C
      codex login status 2>&1 | grep -Fq "Logged in using ChatGPT" || {
        echo "blocked: Codex is not authenticated with ChatGPT" >&2
        exit 5
      }
      codex exec --ignore-user-config --ephemeral --sandbox workspace-write "$FULL_TASK"
    ) >"$REPORT/agent.stdout" 2>"$REPORT/agent.stderr"
    rc=$?
    ;;
esac
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
{"mode":"$MODE","agent_rc":$rc,"validation_rc":$validate_rc,"report":"$REPORT","worktree_kept":${AEGIS_CODER_KEEP_WORKTREE:-0},"cloud_fallback_allowed":$ALLOW_CLOUD_CODEX,"project_context_required":$PROJECT_CONTEXT_REQUIRED,"context_channel":"$CONTEXT_CHANNEL"}
EOF
cat "$REPORT/result.json"

if [[ "$rc" -ne 0 || "$validate_rc" -ne 0 ]]; then
  exit 20
fi
exit 0
