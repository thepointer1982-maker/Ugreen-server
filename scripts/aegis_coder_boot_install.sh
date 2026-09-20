#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${AEGIS_CODER_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
UNIT_DIR="$HOME/.config/systemd/user"
STATE_DIR="$HOME/.local/state/aegis-coder-boot"
SERVICE="$UNIT_DIR/aegis-coder-boot.service"
TIMER="$UNIT_DIR/aegis-coder-boot.timer"
PREFER="${AEGIS_CODER_PREFER:-codex-local}"

[[ "$PREFER" == "codex-local" || "$PREFER" == "local" || "$PREFER" == "codex" ]] || {
  echo "AEGIS_CODER_PREFER must be codex-local, local, or codex" >&2
  exit 64
}

for cmd in systemctl bash python3; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd missing" >&2; exit 3; }
done

systemctl --user show-environment >/dev/null 2>&1 || {
  echo "AEGIS_CODER_BOOT status=blocked reason=user-systemd-unavailable" >&2
  exit 4
}

mkdir -p "$UNIT_DIR" "$STATE_DIR"
chmod 700 "$STATE_DIR"

cat > "$SERVICE" <<EOF
[Unit]
Description=AEGIS coder boot guardian
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$REPO_ROOT
Environment=AEGIS_CODER_REPO_ROOT=$REPO_ROOT
Environment=AEGIS_CODER_PREFER=$PREFER
ExecStart=/usr/bin/env bash $REPO_ROOT/scripts/aegis_coder_boot_guard.sh
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=%h/.local/state/aegis-coder-boot
LockPersonality=true
RestrictRealtime=true
RestrictSUIDSGID=true
UMask=0077
EOF

cat > "$TIMER" <<'EOF'
[Unit]
Description=Keep AEGIS coder backends boot-ready

[Timer]
OnBootSec=12s
OnUnitActiveSec=5min
AccuracySec=10s
Persistent=true
Unit=aegis-coder-boot.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now aegis-coder-boot.timer
systemctl --user start aegis-coder-boot.service

echo "AEGIS_CODER_BOOT status=installed prefer=$PREFER"
echo "state=$STATE_DIR/status.json"
systemctl --user --no-pager status aegis-coder-boot.timer || true
