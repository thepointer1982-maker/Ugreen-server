#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${AEGIS_CODER_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MODEL="${AEGIS_LOCAL_CODER_MODEL:-qwen2.5-coder:7b}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
BIN_DIR="${AEGIS_LOCAL_BIN_DIR:-$HOME/.local/bin}"
PROFILE_SRC="$REPO_ROOT/config/codex/aegis-local.config.toml"
PROFILE_DST="$CODEX_HOME/aegis-local.config.toml"
WRAPPER="$BIN_DIR/aegis-codex-local"
STATE_DIR="${AEGIS_CODEX_OSS_STATE_DIR:-$HOME/.local/state/aegis-codex-oss}"
STATE_FILE="$STATE_DIR/status.json"
ALLOW_MODEL_DOWNLOAD="${AEGIS_ALLOW_MODEL_DOWNLOAD:-0}"

for cmd in codex curl python3; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd missing" >&2; exit 3; }
done

codex --help 2>&1 | grep -q -- "--oss" || {
  echo "blocked: installed Codex CLI does not expose --oss; update Codex CLI first" >&2
  exit 4
}
codex --help 2>&1 | grep -q -- "--local-provider" || {
  echo "blocked: installed Codex CLI does not expose --local-provider" >&2
  exit 4
}

[[ -f "$PROFILE_SRC" ]] || { echo "missing profile: $PROFILE_SRC" >&2; exit 5; }

mkdir -p "$CODEX_HOME" "$BIN_DIR" "$STATE_DIR"
chmod 700 "$CODEX_HOME" "$STATE_DIR" 2>/dev/null || true
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
    raise SystemExit(0 if model in names else 1)
except Exception:
    raise SystemExit(1)
PY
  then
    model_ready=1
  fi
fi

if [[ "$model_ready" -ne 1 && "$ALLOW_MODEL_DOWNLOAD" == "1" && -x "$(command -v ollama 2>/dev/null || true)" ]]; then
  echo "Local model missing; downloading free Ollama model: $MODEL"
  ollama pull "$MODEL"
  model_ready=1
fi

cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
MODEL="${AEGIS_LOCAL_CODER_MODEL:-$MODEL}"
unset OPENAI_API_KEY OPENAI_ORG_ID OPENAI_PROJECT_ID OPENAI_BASE_URL CODEX_API_KEY
export CODEX_HOME="${CODEX_HOME:-$CODEX_HOME}"
if [[ "${1:-}" == "exec" ]]; then
  shift
  exec codex exec --oss --local-provider ollama --model "$MODEL" --profile aegis-local "$@"
fi
exec codex --oss --local-provider ollama --model "$MODEL" --profile aegis-local "$@"
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
  "ollama_ready":os.environ["AEGIS_CODEX_OSS_OLLAMA_READY"] == "1",
  "model_ready":os.environ["AEGIS_CODEX_OSS_MODEL_READY"] == "1",
  "wrapper":os.environ["AEGIS_CODEX_OSS_WRAPPER"],
  "profile":os.environ["AEGIS_CODEX_OSS_PROFILE"],
  "cloud_model_usage":False,
  "openai_api_key_required":False,
  "web_search":"disabled",
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
