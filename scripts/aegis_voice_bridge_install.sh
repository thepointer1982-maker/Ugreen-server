#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${AEGIS_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
UNIT_DIR="$HOME/.config/systemd/user"
STATE_DIR="${AEGIS_VOICE_STATE_DIR:-$HOME/.local/state/aegis-voice}"
SOCKET_PATH="${AEGIS_VOICE_SOCKET:-$STATE_DIR/bridge.sock}"

for cmd in python3 systemctl; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "$cmd missing" >&2
    exit 3
  }
done

systemctl --user show-environment >/dev/null 2>&1 || {
  echo "AEGIS_VOICE status=blocked reason=user-systemd-unavailable" >&2
  exit 4
}

mkdir -p "$UNIT_DIR" "$STATE_DIR"
chmod 700 "$STATE_DIR"

cat > "$UNIT_DIR/aegis-voice-bridge.service" <<EOF
[Unit]
Description=AEGIS local Alexa/voice Unix-socket bridge
After=aegis-autonomy.service

[Service]
Type=simple
WorkingDirectory=$REPO_ROOT
Environment=AEGIS_REPO_ROOT=$REPO_ROOT
Environment=AEGIS_VOICE_STATE_DIR=$STATE_DIR
Environment=AEGIS_VOICE_SOCKET=$SOCKET_PATH
ExecStart=/usr/bin/env python3 $REPO_ROOT/scripts/aegis_voice_bridge.py serve
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$STATE_DIR %h/.local/state/aegis-project %h/.local/state/aegis-autonomy
RestrictAddressFamilies=AF_UNIX
LockPersonality=true
RestrictRealtime=true
RestrictSUIDSGID=true
UMask=0077

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now aegis-voice-bridge.service

for _ in 1 2 3 4 5; do
  [[ -S "$SOCKET_PATH" ]] && break
  sleep 1
done

[[ -S "$SOCKET_PATH" ]] || {
  echo "AEGIS_VOICE status=blocked reason=socket-not-ready" >&2
  exit 5
}

echo "AEGIS_VOICE status=healthy transport=unix-socket socket=$SOCKET_PATH"
