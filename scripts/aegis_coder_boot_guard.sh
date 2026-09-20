#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${AEGIS_CODER_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
STATE_DIR="${AEGIS_CODER_BOOT_STATE_DIR:-$HOME/.local/state/aegis-coder-boot}"
STATE_FILE="$STATE_DIR/status.json"
PREFER="${AEGIS_CODER_PREFER:-local}"
LOCAL_MODEL="${AEGIS_LOCAL_CODER_MODEL:-qwen2.5-coder:7b}"
CONFIG="$REPO_ROOT/config/opencode/aegis-local.json"

[[ "$PREFER" == "local" || "$PREFER" == "codex" ]] || {
  echo "invalid AEGIS_CODER_PREFER=$PREFER" >&2
  exit 64
}

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true

# Zero-extra-cost contract: never inherit API-key authentication into Codex.
unset OPENAI_API_KEY
unset OPENAI_ORG_ID
unset OPENAI_PROJECT_ID
unset OPENAI_BASE_URL
unset CODEX_API_KEY

codex_cli="missing"
codex_auth="unavailable"
codex_ready=0
codex_status_rc=127

if command -v codex >/dev/null 2>&1; then
  codex_cli="installed"
  set +e
  if command -v timeout >/dev/null 2>&1; then
    codex_status="$(timeout 15s codex login status 2>&1)"
    codex_status_rc=$?
  else
    codex_status="$(codex login status 2>&1)"
    codex_status_rc=$?
  fi
  set -e

  if grep -Fq "Logged in using ChatGPT" <<<"$codex_status"; then
    codex_auth="chatgpt"
    codex_ready=1
  elif [[ "$codex_status_rc" -eq 0 ]]; then
    codex_auth="non-chatgpt"
  else
    codex_auth="not-logged-in"
  fi
fi

opencode_ready=0
command -v opencode >/dev/null 2>&1 && opencode_ready=1

ollama_ready=0
if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  ollama_ready=1
else
  # If the local daemon is installed as an AEGIS user unit, try to recover it.
  if command -v systemctl >/dev/null 2>&1 && systemctl --user cat aegis-ollama.service >/dev/null 2>&1; then
    systemctl --user start aegis-ollama.service >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5; do
      sleep 1
      if curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        ollama_ready=1
        break
      fi
    done
  fi
fi

config_ready=0
[[ -f "$CONFIG" ]] && config_ready=1

local_ready=0
if [[ "$opencode_ready" -eq 1 && "$ollama_ready" -eq 1 && "$config_ready" -eq 1 ]]; then
  local_ready=1
fi

selected="none"
health="blocked"
reason="no-coder-backend-ready"

if [[ "$PREFER" == "codex" ]]; then
  if [[ "$codex_ready" -eq 1 ]]; then
    selected="codex"
    health="healthy"
    reason="preferred-codex-ready"
  elif [[ "$local_ready" -eq 1 ]]; then
    selected="local"
    health="degraded"
    reason="codex-unavailable-local-fallback-ready"
  fi
else
  if [[ "$local_ready" -eq 1 ]]; then
    selected="local"
    health="healthy"
    reason="preferred-local-ready"
  elif [[ "$codex_ready" -eq 1 ]]; then
    selected="codex"
    health="degraded"
    reason="local-unavailable-codex-chatgpt-fallback-ready"
  fi
fi

export AEGIS_CODER_BOOT_STATE_FILE="$STATE_FILE"
export AEGIS_CODER_BOOT_HEALTH="$health"
export AEGIS_CODER_BOOT_REASON="$reason"
export AEGIS_CODER_BOOT_SELECTED="$selected"
export AEGIS_CODER_BOOT_PREFER="$PREFER"
export AEGIS_CODER_BOOT_CODEX_CLI="$codex_cli"
export AEGIS_CODER_BOOT_CODEX_AUTH="$codex_auth"
export AEGIS_CODER_BOOT_CODEX_READY="$codex_ready"
export AEGIS_CODER_BOOT_CODEX_RC="$codex_status_rc"
export AEGIS_CODER_BOOT_OPENCODE_READY="$opencode_ready"
export AEGIS_CODER_BOOT_OLLAMA_READY="$ollama_ready"
export AEGIS_CODER_BOOT_CONFIG_READY="$config_ready"
export AEGIS_CODER_BOOT_LOCAL_READY="$local_ready"
export AEGIS_CODER_BOOT_MODEL="$LOCAL_MODEL"

python3 - <<'PY'
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["AEGIS_CODER_BOOT_STATE_FILE"]).expanduser()
data = {
    "schema": "aegis-coder-boot/v1",
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "health": os.environ["AEGIS_CODER_BOOT_HEALTH"],
    "reason": os.environ["AEGIS_CODER_BOOT_REASON"],
    "preferred": os.environ["AEGIS_CODER_BOOT_PREFER"],
    "selected": os.environ["AEGIS_CODER_BOOT_SELECTED"],
    "codex": {
        "cli": os.environ["AEGIS_CODER_BOOT_CODEX_CLI"],
        "auth": os.environ["AEGIS_CODER_BOOT_CODEX_AUTH"],
        "ready": os.environ["AEGIS_CODER_BOOT_CODEX_READY"] == "1",
        "status_rc": int(os.environ["AEGIS_CODER_BOOT_CODEX_RC"]),
        "api_key_auth_allowed": False,
    },
    "local": {
        "ready": os.environ["AEGIS_CODER_BOOT_LOCAL_READY"] == "1",
        "opencode_ready": os.environ["AEGIS_CODER_BOOT_OPENCODE_READY"] == "1",
        "ollama_loopback_ready": os.environ["AEGIS_CODER_BOOT_OLLAMA_READY"] == "1",
        "config_ready": os.environ["AEGIS_CODER_BOOT_CONFIG_READY"] == "1",
        "model": os.environ["AEGIS_CODER_BOOT_MODEL"],
    },
    "boot_policy": {
        "runs_model_task_at_boot": False,
        "fallback_is_local_first": os.environ["AEGIS_CODER_BOOT_PREFER"] == "local",
        "external_api_key_required": False,
    },
}
path.parent.mkdir(parents=True, exist_ok=True)
fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
finally:
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
print(json.dumps(data, ensure_ascii=False))
PY

# Health is carried in state.json. Return success so the timer keeps self-healing.
exit 0
