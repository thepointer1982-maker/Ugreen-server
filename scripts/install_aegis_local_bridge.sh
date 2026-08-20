#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_SRC="$REPO_ROOT/systemd/aegis-local-bridge.service"
TIMER_SRC="$REPO_ROOT/systemd/aegis-local-bridge.timer"
SERVICE_DST="/etc/systemd/system/aegis-local-bridge.service"
TIMER_DST="/etc/systemd/system/aegis-local-bridge.timer"
ENV_DIR="/etc/aegis"
ENV_FILE="$ENV_DIR/bridge.env"
RUN_USER="${SUDO_USER:-$(id -un)}"
RUN_GROUP="$(id -gn "$RUN_USER")"

[[ -f "$SERVICE_SRC" && -f "$TIMER_SRC" ]] || { echo "AEGIS_BRIDGE_INSTALL status=error reason=templates_missing" >&2; exit 2; }
command -v systemctl >/dev/null 2>&1 || { echo "AEGIS_BRIDGE_INSTALL status=error reason=systemd_missing" >&2; exit 2; }
command -v sudo >/dev/null 2>&1 || { echo "AEGIS_BRIDGE_INSTALL status=error reason=sudo_missing" >&2; exit 2; }

TMP_SERVICE="$(mktemp)"
trap 'rm -f "$TMP_SERVICE"' EXIT
python3 - "$SERVICE_SRC" "$TMP_SERVICE" "$REPO_ROOT" "$RUN_USER" "$RUN_GROUP" <<'PY'
from pathlib import Path
import sys
src, dst, root, user, group = sys.argv[1:]
text = Path(src).read_text(encoding='utf-8')
for old, new in {
    '@AEGIS_REPO_ROOT@': root,
    '@AEGIS_USER@': user,
    '@AEGIS_GROUP@': group,
}.items():
    text = text.replace(old, new)
Path(dst).write_text(text, encoding='utf-8')
PY

sudo install -d -m 0750 "$ENV_DIR"
if [[ ! -f "$ENV_FILE" ]]; then
  printf '%s\n' \
    "AEGIS_REPO_ROOT=$REPO_ROOT" \
    "AEGIS_BRIDGE_PUSH=0" | sudo tee "$ENV_FILE" >/dev/null
  sudo chmod 0640 "$ENV_FILE"
fi
sudo install -m 0644 "$TMP_SERVICE" "$SERVICE_DST"
sudo install -m 0644 "$TIMER_SRC" "$TIMER_DST"
sudo systemctl daemon-reload
sudo systemctl enable --now aegis-local-bridge.timer

echo "AEGIS_BRIDGE_INSTALL status=installed"
echo "repo_root=$REPO_ROOT"
echo "run_user=$RUN_USER"
echo "push_enabled=0"
echo "timer=aegis-local-bridge.timer"
echo "To enable guarded GitHub export pushes later, set AEGIS_BRIDGE_PUSH=1 in $ENV_FILE."
