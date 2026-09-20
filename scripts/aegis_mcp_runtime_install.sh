#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${AEGIS_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RUNTIME_ROOT="${AEGIS_MCP_RUNTIME_ROOT:-$HOME/.local/share/aegis-mcp}"
VENV="$RUNTIME_ROOT/venv"
BIN_DIR="${AEGIS_LOCAL_BIN_DIR:-$HOME/.local/bin}"
WRAPPER="$BIN_DIR/aegis-mcp-local"
STATE_DIR="${AEGIS_MCP_STATE_DIR:-$HOME/.local/state/aegis-mcp}"
STATE_FILE="$STATE_DIR/runtime.json"
ALLOW_INSTALL="${AEGIS_ALLOW_MCP_INSTALL:-0}"

for cmd in python3; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd missing" >&2; exit 3; }
done

mkdir -p "$RUNTIME_ROOT" "$BIN_DIR" "$STATE_DIR"
chmod 700 "$RUNTIME_ROOT" "$STATE_DIR" 2>/dev/null || true

MCP_PYTHON=""
install_performed=0

if python3 - <<'PY' >/dev/null 2>&1
from mcp.server import MCPServer
PY
then
  MCP_PYTHON="$(command -v python3)"
elif [[ -x "$VENV/bin/python" ]] && "$VENV/bin/python" - <<'PY' >/dev/null 2>&1
from mcp.server import MCPServer
PY
then
  MCP_PYTHON="$VENV/bin/python"
elif [[ "$ALLOW_INSTALL" == "1" ]]; then
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install --disable-pip-version-check --no-input "mcp[cli]>=2,<3"
  "$VENV/bin/python" - <<'PY' >/dev/null
from mcp.server import MCPServer
PY
  MCP_PYTHON="$VENV/bin/python"
  install_performed=1
fi

health="blocked"
reason="mcp-python-runtime-missing"
if [[ -n "$MCP_PYTHON" ]]; then
  health="healthy"
  reason="mcp-runtime-ready"
fi

if [[ -n "$MCP_PYTHON" ]]; then
  cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export AEGIS_REPO_ROOT="$REPO_ROOT"
export AEGIS_PROJECT_STATE_DIR="${AEGIS_PROJECT_STATE_DIR:-$HOME/.local/state/aegis-project}"
exec "$MCP_PYTHON" "$REPO_ROOT/scripts/aegis_mcp_server.py"
EOF
  chmod 700 "$WRAPPER"
fi

export AEGIS_MCP_STATE_FILE="$STATE_FILE"
export AEGIS_MCP_HEALTH="$health"
export AEGIS_MCP_REASON="$reason"
export AEGIS_MCP_PYTHON="$MCP_PYTHON"
export AEGIS_MCP_WRAPPER="$WRAPPER"
export AEGIS_MCP_INSTALL_PERFORMED="$install_performed"

python3 - <<'PY'
import json, os, tempfile
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["AEGIS_MCP_STATE_FILE"])
data = {
    "schema": "aegis-mcp-runtime/v1",
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "health": os.environ["AEGIS_MCP_HEALTH"],
    "reason": os.environ["AEGIS_MCP_REASON"],
    "python": os.environ["AEGIS_MCP_PYTHON"] or None,
    "wrapper": os.environ["AEGIS_MCP_WRAPPER"],
    "install_performed": os.environ["AEGIS_MCP_INSTALL_PERFORMED"] == "1",
    "transport": "stdio",
    "network_listener": False,
    "public_port": False,
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

[[ "$health" == "healthy" ]]
