#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="${AEGIS_PERSISTENCE_STATE_DIR:-$HOME/.local/state/aegis-persistence}"
STATE_FILE="$STATE_DIR/status.json"
USER_NAME="$(id -un)"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true

linger_state="unknown"
method="none"
reason="loginctl-unavailable"

read_linger() {
  if ! command -v loginctl >/dev/null 2>&1; then
    printf 'unknown'
    return 0
  fi
  local v
  v="$(loginctl show-user "$USER_NAME" -p Linger --value 2>/dev/null || true)"
  case "$v" in
    yes|no) printf '%s' "$v" ;;
    *) printf 'unknown' ;;
  esac
}

linger_state="$(read_linger)"
if [[ "$linger_state" == "yes" ]]; then
  method="already-enabled"
  reason="persistent-user-manager-ready"
elif command -v loginctl >/dev/null 2>&1; then
  if loginctl enable-linger "$USER_NAME" >/dev/null 2>&1; then
    linger_state="$(read_linger)"
    method="loginctl"
  elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    if sudo -n loginctl enable-linger "$USER_NAME" >/dev/null 2>&1; then
      linger_state="$(read_linger)"
      method="sudo-n-loginctl"
    fi
  fi

  if [[ "$linger_state" == "yes" ]]; then
    reason="persistent-user-manager-enabled"
  else
    reason="linger-not-enabled-no-noninteractive-privilege"
  fi
fi

user_manager="unavailable"
if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  user_manager="available"
fi

health="degraded"
if [[ "$linger_state" == "yes" && "$user_manager" == "available" ]]; then
  health="healthy"
elif [[ "$user_manager" == "unavailable" ]]; then
  health="blocked"
  reason="user-systemd-unavailable"
fi

export AEGIS_PERSIST_STATE_FILE="$STATE_FILE"
export AEGIS_PERSIST_HEALTH="$health"
export AEGIS_PERSIST_REASON="$reason"
export AEGIS_PERSIST_LINGER="$linger_state"
export AEGIS_PERSIST_METHOD="$method"
export AEGIS_PERSIST_MANAGER="$user_manager"

python3 - <<'PY'
import json, os, tempfile
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["AEGIS_PERSIST_STATE_FILE"]).expanduser()
data = {
    "schema": "aegis-user-persistence/v1",
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "health": os.environ["AEGIS_PERSIST_HEALTH"],
    "reason": os.environ["AEGIS_PERSIST_REASON"],
    "linger": os.environ["AEGIS_PERSIST_LINGER"],
    "method": os.environ["AEGIS_PERSIST_METHOD"],
    "user_manager": os.environ["AEGIS_PERSIST_MANAGER"],
    "security": {
        "opened_router_ports": False,
        "stored_password": False,
        "stored_token": False,
        "noninteractive_privilege_only": True,
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
