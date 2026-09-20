#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${AEGIS_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
UNIT_DIR="$HOME/.config/systemd/user"
STATE_DIR="${AEGIS_AUTONOMY_STATE_DIR:-$HOME/.local/state/aegis-autonomy}"
POLICY="${AEGIS_AUTONOMY_POLICY:-$REPO_ROOT/config/autonomy/aegis-max-local.json}"
SERVICE="$UNIT_DIR/aegis-autonomy.service"
TIMER="$UNIT_DIR/aegis-autonomy.timer"

for cmd in python3 systemctl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd missing" >&2; exit 3; }
done

[[ -s "$POLICY" ]] || { echo "autonomy policy missing: $POLICY" >&2; exit 4; }
systemctl --user show-environment >/dev/null 2>&1 || {
  echo "AEGIS_AUTONOMY status=blocked reason=user-systemd-unavailable" >&2
  exit 5
}

mkdir -p "$UNIT_DIR" "$STATE_DIR" "$HOME/.local/share/aegis-coder"
chmod 700 "$STATE_DIR" 2>/dev/null || true

cat > "$SERVICE" <<EOF
[Unit]
Description=AEGIS AUTO-MAX-LOCAL autonomy supervisor
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$REPO_ROOT
Environment=AEGIS_REPO_ROOT=$REPO_ROOT
Environment=AEGIS_AUTONOMY_POLICY=$POLICY
Environment=AEGIS_AUTONOMY_STATE_DIR=$STATE_DIR
Environment=AEGIS_ALLOW_CLOUD_CODEX=0
Environment=AEGIS_PROJECT_CONTEXT_REQUIRED=1
ExecStart=/usr/bin/env python3 $REPO_ROOT/scripts/aegis_autonomy_supervisor.py cycle
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$STATE_DIR %h/.local/state %h/.local/share/aegis-coder
LockPersonality=true
RestrictRealtime=true
RestrictSUIDSGID=true
UMask=0077

[Install]
WantedBy=default.target
EOF

cat > "$TIMER" <<'EOF'
[Unit]
Description=Run AEGIS AUTO-MAX-LOCAL autonomy every 2 minutes

[Timer]
OnBootSec=35s
OnUnitActiveSec=2min
AccuracySec=10s
Persistent=true
Unit=aegis-autonomy.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now aegis-autonomy.timer
systemctl --user start aegis-autonomy.service || true

timer_active=0
if systemctl --user is-active --quiet aegis-autonomy.timer; then
  timer_active=1
fi

printf 'AEGIS_AUTONOMY profile=AUTO-MAX-LOCAL timer_active=%s state=%s/status.json\n'   "$timer_active" "$STATE_DIR"

[[ "$timer_active" -eq 1 ]]
