#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${AEGIS_CODER_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MODEL="${AEGIS_LOCAL_CODER_MODEL:-qwen2.5-coder:7b}"
BIN_DIR="${AEGIS_LOCAL_BIN_DIR:-$HOME/.local/bin}"
OSS_HOME="${AEGIS_CODEX_OSS_HOME:-$HOME/.local/share/aegis-codex-oss/home}"
PROFILE_SRC="$REPO_ROOT/config/codex/aegis-local.config.toml"
PROFILE_DST="$OSS_HOME/aegis-local.config.toml"
WRAPPER="$BIN_DIR/aegis-codex-local"
STATE_DIR="${AEGIS_CODEX_OSS_STATE_DIR:-$HOME/.local/state/aegis-codex-oss}"
STATE_FILE="$STATE_DIR/status.json"
ALLOW_MODEL_DOWNLOAD="${AEGIS_ALLOW_MODEL_DOWNLOAD:-0}"
ALLOW_CODEX_INSTALL="${AEGIS_ALLOW_CODEX_INSTALL:-0}"

for cmd in curl python3; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd missing" >&2; exit 3; }
done

mkdir -p "$BIN_DIR" "$OSS_HOME" "$STATE_DIR"
chmod 700 "$OSS_HOME" "$STATE_DIR" 2>/dev/null || true
export PATH="$BIN_DIR:$PATH"

codex_installed=0
if command -v codex >/dev/null 2>&1; then
  codex_installed=1
elif [[ "$ALLOW_CODEX_INSTALL" == "1" ]]; then
  tmp_installer="$(mktemp)"
  trap 'rm -f "$tmp_installer"' EXIT
  curl -fsSL --connect-timeout 10 --max-time 60 https://chatgpt.com/codex/install.sh -o "$tmp_installer"
  grep -q "CODEX_INSTALL_DIR" "$tmp_installer" || {
    echo "blocked: unexpected Codex installer content" >&2
    exit 6
  }
  CODEX_NON_INTERACTIVE=1 CODEX_INSTALL_DIR="$BIN_DIR" sh "$tmp_installer"
  rm -f "$tmp_installer"
  trap - EXIT
  command -v codex >/dev/null 2>&1 || {
    echo "blocked: Codex install completed but codex is not on PATH" >&2
    exit 6
  }
  codex_installed=1
fi

if [[ "$codex_installed" -ne 1 ]]; then
  echo "blocked: Codex CLI missing; set AEGIS_ALLOW_CODEX_INSTALL=1 for the official user-local installer" >&2
  exit 3
fi

codex --help 2>&1 | grep -q -- "--oss" || {
  echo "blocked: installed Codex CLI does not expose --oss; update Codex CLI first" >&2
  exit 4
}
codex --help 2>&1 | grep -q -- "--local-provider" || {
  echo "blocked: installed Codex CLI does not expose --local-provider" >&2
  exit 4
}

[[ -f "$PROFILE_SRC" ]] || { echo "missing profile: $PROFILE_SRC" >&2; exit 5; }
install -m 600 "$PROFILE_SRC" "$PROFILE_DST"

ollama_ready=0
if curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  ollama_ready=1
fi

model_ready=0
if [[ "$ollama_ready" -eq 1 ]]; then
  if python3 - "$MODEL" <<'PY'
import json, sys, urllib.request
model = sys.argv[1]
try:
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as r:
        data = json.load(r)
    names = {str(x.get("name","")) for x in data.get("models",[]) if isinstance(x,dict)}
    aliases = {n.split("@",1)[0] for n in names}
    raise SystemExit(0 if model in names or model in aliases else 1)
except Exception:
    raise SystemExit(1)
PY
  then
    model_ready=1
  fi
fi

if [[ "$model_ready" -ne 1 && "$ALLOW_MODEL_DOWNLOAD" == "1" ]]; then
  command -v ollama >/dev/null 2>&1 || {
    echo "blocked: ollama CLI missing; cannot download local model" >&2
    exit 7
  }
  echo "Local model missing; downloading free Ollama model: $MODEL"
  ollama pull "$MODEL"
  model_ready=1
fi

cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
MODEL="${AEGIS_LOCAL_CODER_MODEL:-$MODEL}"
unset OPENAI_API_KEY OPENAI_ORG_ID OPENAI_PROJECT_ID OPENAI_BASE_URL CODEX_API_KEY CODEX_ACCESS_TOKEN
export CODEX_HOME="$OSS_HOME"
export PATH="$BIN_DIR:\$PATH"
if [[ "${1:-}" == "exec" ]]; then
  shift
  exec codex exec --oss --local-provider ollama --model "\$MODEL" --profile aegis-local --sandbox workspace-write --ask-for-approval never --ephemeral -c 'web_search="disabled"' "\$@"
fi
exec codex --oss --local-provider ollama --model "\$MODEL" --profile aegis-local --sandbox workspace-write --ask-for-approval never -c 'web_search="disabled"' "\$@"
EOF
chmod 700 "$WRAPPER"

health="blocked"
reason="ollama-unreachable"
if [[ "$ollama_ready" -eq 1 && "$model_ready" -eq 1 ]]; then
  health="healthy"
  reason="codex-oss-ollama-ready"
elif [[ "$ollama_ready" -eq 1 ]]; then
  health="degraded"
  reason="local-model-missing"
fi

export AEGIS_CODEX_OSS_STATE_FILE="$STATE_FILE"
export AEGIS_CODEX_OSS_HEALTH="$health"
export AEGIS_CODEX_OSS_REASON="$reason"
export AEGIS_CODEX_OSS_MODEL="$MODEL"
export AEGIS_CODEX_OSS_OLLAMA_READY="$ollama_ready"
export AEGIS_CODEX_OSS_MODEL_READY="$model_ready"
export AEGIS_CODEX_OSS_WRAPPER="$WRAPPER"
export AEGIS_CODEX_OSS_PROFILE="$PROFILE_DST"
export AEGIS_CODEX_OSS_HOME="$OSS_HOME"
export AEGIS_CODEX_OSS_BIN="$(command -v codex)"

python3 - <<'PY'
import json, os, tempfile
from datetime import datetime, timezone
from pathlib import Path
path = Path(os.environ["AEGIS_CODEX_OSS_STATE_FILE"])
data = {
  "schema":"aegis-codex-oss/v1",
  "checked_at":datetime.now(timezone.utc).isoformat(),
  "health":os.environ["AEGIS_CODEX_OSS_HEALTH"],
  "reason":os.environ["AEGIS_CODEX_OSS_REASON"],
  "provider":"ollama",
  "model":os.environ["AEGIS_CODEX_OSS_MODEL"],
  "ollama_endpoint":"http://127.0.0.1:11434",
  "ollama_ready":os.environ["AEGIS_CODEX_OSS_OLLAMA_READY"] == "1",
  "model_ready":os.environ["AEGIS_CODEX_OSS_MODEL_READY"] == "1",
  "wrapper":os.environ["AEGIS_CODEX_OSS_WRAPPER"],
  "profile":os.environ["AEGIS_CODEX_OSS_PROFILE"],
  "isolated_codex_home":os.environ["AEGIS_CODEX_OSS_HOME"],
  "codex_binary":os.environ["AEGIS_CODEX_OSS_BIN"],
  "cloud_model_usage":False,
  "chatgpt_auth_required":False,
  "openai_api_key_required":False,
  "web_search":"disabled",
  "shell_network_access":False,
}
path.parent.mkdir(parents=True, exist_ok=True)
fd,tmp=tempfile.mkstemp(prefix=path.name+".",dir=str(path.parent))
try:
  with os.fdopen(fd,"w",encoding="utf-8") as f:
    json.dump(data,f,indent=2,sort_keys=True); f.write("\n"); f.flush(); os.fsync(f.fileno())
  os.replace(tmp,path)
finally:
  try: os.unlink(tmp)
  except FileNotFoundError: pass
print(json.dumps(data,ensure_ascii=False))
PY

echo "AEGIS_CODEX_OSS health=$health model=$MODEL wrapper=$WRAPPER"
[[ "$health" != "blocked" ]]
