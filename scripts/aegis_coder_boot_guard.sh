#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${AEGIS_CODER_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
STATE_DIR="${AEGIS_CODER_BOOT_STATE_DIR:-$HOME/.local/state/aegis-coder-boot}"
STATE_FILE="$STATE_DIR/status.json"
PREFER="${AEGIS_CODER_PREFER:-codex-local}"
LOCAL_MODEL="${AEGIS_LOCAL_CODER_MODEL:-qwen2.5-coder:7b}"
CONFIG="$REPO_ROOT/config/opencode/aegis-local.json"
CODEX_OSS_STATE="${AEGIS_CODEX_OSS_STATE_FILE:-$HOME/.local/state/aegis-codex-oss/status.json}"
LOCAL_CODEX_WRAPPER="${AEGIS_CODEX_OSS_WRAPPER:-$HOME/.local/bin/aegis-codex-local}"
ALLOW_CLOUD_CODEX="${AEGIS_ALLOW_CLOUD_CODEX:-0}"

[[ "$PREFER" == "codex-local" || "$PREFER" == "local" || "$PREFER" == "codex" ]] || {
  echo "invalid AEGIS_CODER_PREFER=$PREFER" >&2
  exit 64
}

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true

# Zero-cloud default: never inherit API-key authentication into any automatic
# coder path.
unset OPENAI_API_KEY
unset OPENAI_ORG_ID
unset OPENAI_PROJECT_ID
unset OPENAI_BASE_URL
unset CODEX_API_KEY
unset CODEX_ACCESS_TOKEN

ollama_ready=0
if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  ollama_ready=1
else
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

codex_oss_ready=0
codex_oss_reason="state-missing"
if [[ -s "$CODEX_OSS_STATE" && -x "$LOCAL_CODEX_WRAPPER" && "$ollama_ready" -eq 1 ]]; then
  set +e
  codex_oss_reason="$(python3 - "$CODEX_OSS_STATE" <<'PY'
import json, sys
from pathlib import Path
try:
    d=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    ok=(
        d.get("health") == "healthy"
        and d.get("provider") == "ollama"
        and d.get("model_ready") is True
        and d.get("cloud_model_usage") is False
        and d.get("openai_api_key_required") is False
    )
    print("ready" if ok else str(d.get("reason") or "not-ready"))
    raise SystemExit(0 if ok else 1)
except Exception as e:
    print("invalid-state")
    raise SystemExit(1)
PY
)"
  codex_oss_rc=$?
  set -e
  [[ "$codex_oss_rc" -eq 0 ]] && codex_oss_ready=1
fi

opencode_ready=0
command -v opencode >/dev/null 2>&1 && opencode_ready=1
config_ready=0
[[ -f "$CONFIG" ]] && config_ready=1
local_ready=0
if [[ "$opencode_ready" -eq 1 && "$ollama_ready" -eq 1 && "$config_ready" -eq 1 ]]; then
  local_ready=1
fi

codex_cli="missing"
codex_auth="unavailable"
codex_cloud_ready=0
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
    codex_cloud_ready=1
  elif [[ "$codex_status_rc" -eq 0 ]]; then
    codex_auth="non-chatgpt"
  else
    codex_auth="not-logged-in"
  fi
fi

selected="none"
health="blocked"
reason="no-coder-backend-ready"

choose_local_first() {
  if [[ "$codex_oss_ready" -eq 1 ]]; then
    selected="codex-local"
    health="healthy"
    reason="codex-oss-ollama-ready"
  elif [[ "$local_ready" -eq 1 ]]; then
    selected="local"
    health="degraded"
    reason="codex-local-unavailable-opencode-fallback-ready"
  elif [[ "$ALLOW_CLOUD_CODEX" == "1" && "$codex_cloud_ready" -eq 1 ]]; then
    selected="codex"
    health="degraded"
    reason="local-backends-unavailable-cloud-codex-fallback-ready"
  fi
}

case "$PREFER" in
  codex-local)
    choose_local_first
    ;;
  local)
    if [[ "$local_ready" -eq 1 ]]; then
      selected="local"
      health="healthy"
      reason="preferred-opencode-local-ready"
    elif [[ "$codex_oss_ready" -eq 1 ]]; then
      selected="codex-local"
      health="degraded"
      reason="opencode-unavailable-codex-local-ready"
    elif [[ "$ALLOW_CLOUD_CODEX" == "1" && "$codex_cloud_ready" -eq 1 ]]; then
      selected="codex"
      health="degraded"
      reason="local-backends-unavailable-cloud-codex-fallback-ready"
    fi
    ;;
  codex)
    if [[ "$codex_cloud_ready" -eq 1 ]]; then
      selected="codex"
      health="healthy"
      reason="preferred-cloud-codex-ready"
    else
      choose_local_first
      if [[ "$selected" != "none" ]]; then
        health="degraded"
        reason="cloud-codex-unavailable-local-fallback-ready"
      fi
    fi
    ;;
esac

export AEGIS_CODER_BOOT_STATE_FILE="$STATE_FILE"
export AEGIS_CODER_BOOT_HEALTH="$health"
export AEGIS_CODER_BOOT_REASON="$reason"
export AEGIS_CODER_BOOT_SELECTED="$selected"
export AEGIS_CODER_BOOT_PREFER="$PREFER"
export AEGIS_CODER_BOOT_CODEX_OSS_READY="$codex_oss_ready"
export AEGIS_CODER_BOOT_CODEX_OSS_REASON="$codex_oss_reason"
export AEGIS_CODER_BOOT_CODEX_CLI="$codex_cli"
export AEGIS_CODER_BOOT_CODEX_AUTH="$codex_auth"
export AEGIS_CODER_BOOT_CODEX_CLOUD_READY="$codex_cloud_ready"
export AEGIS_CODER_BOOT_CODEX_RC="$codex_status_rc"
export AEGIS_CODER_BOOT_OPENCODE_READY="$opencode_ready"
export AEGIS_CODER_BOOT_OLLAMA_READY="$ollama_ready"
export AEGIS_CODER_BOOT_CONFIG_READY="$config_ready"
export AEGIS_CODER_BOOT_LOCAL_READY="$local_ready"
export AEGIS_CODER_BOOT_MODEL="$LOCAL_MODEL"
export AEGIS_CODER_BOOT_ALLOW_CLOUD="$ALLOW_CLOUD_CODEX"

python3 - <<'PY'
import json, os, tempfile
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["AEGIS_CODER_BOOT_STATE_FILE"]).expanduser()
data = {
    "schema": "aegis-coder-boot/v2",
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "health": os.environ["AEGIS_CODER_BOOT_HEALTH"],
    "reason": os.environ["AEGIS_CODER_BOOT_REASON"],
    "preferred": os.environ["AEGIS_CODER_BOOT_PREFER"],
    "selected": os.environ["AEGIS_CODER_BOOT_SELECTED"],
    "codex_local": {
        "ready": os.environ["AEGIS_CODER_BOOT_CODEX_OSS_READY"] == "1",
        "reason": os.environ["AEGIS_CODER_BOOT_CODEX_OSS_REASON"],
        "provider": "ollama",
        "model": os.environ["AEGIS_CODER_BOOT_MODEL"],
        "cloud_model_usage": False,
        "api_key_auth_allowed": False,
    },
    "codex_cloud": {
        "cli": os.environ["AEGIS_CODER_BOOT_CODEX_CLI"],
        "auth": os.environ["AEGIS_CODER_BOOT_CODEX_AUTH"],
        "ready": os.environ["AEGIS_CODER_BOOT_CODEX_CLOUD_READY"] == "1",
        "status_rc": int(os.environ["AEGIS_CODER_BOOT_CODEX_RC"]),
        "automatic_fallback_allowed": os.environ["AEGIS_CODER_BOOT_ALLOW_CLOUD"] == "1",
        "api_key_auth_allowed": False,
    },
    "opencode_local": {
        "ready": os.environ["AEGIS_CODER_BOOT_LOCAL_READY"] == "1",
        "opencode_ready": os.environ["AEGIS_CODER_BOOT_OPENCODE_READY"] == "1",
        "ollama_loopback_ready": os.environ["AEGIS_CODER_BOOT_OLLAMA_READY"] == "1",
        "config_ready": os.environ["AEGIS_CODER_BOOT_CONFIG_READY"] == "1",
        "model": os.environ["AEGIS_CODER_BOOT_MODEL"],
    },
    "boot_policy": {
        "runs_model_task_at_boot": False,
        "local_first": os.environ["AEGIS_CODER_BOOT_PREFER"] in {"codex-local", "local"},
        "external_api_key_required": False,
        "cloud_model_usage_default": False,
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

exit 0
