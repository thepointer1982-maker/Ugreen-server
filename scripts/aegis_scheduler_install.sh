#!/usr/bin/env bash
set -Eeuo pipefail

INTERVAL_MINUTES=15
ENABLE_PUSH=0
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: scripts/aegis_scheduler_install.sh [--interval-minutes N] [--push]

Installs a user-level systemd timer. Default is local-only every 15 minutes.
--push explicitly enables commit+push of verified AEGIS artifacts.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --interval-minutes) INTERVAL_MINUTES="${2:?missing minutes}"; shift 2 ;;
    --push) ENABLE_PUSH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 64 ;;
  esac
done
[[ "$INTERVAL_MINUTES" =~ ^[1-9][0-9]*$ ]] || { echo 'interval must be a positive integer' >&2; exit 64; }
(( INTERVAL_MINUTES >= 5 )) || { echo 'interval below 5 minutes refused' >&2; exit 64; }
command -v systemctl >/dev/null 2>&1 || { echo 'systemd is required for this installer' >&2; exit 2; }
systemctl --user show-environment >/dev/null 2>&1 || { echo 'systemd user manager unavailable' >&2; exit 2; }

UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
SERVICE="$UNIT_DIR/aegis-export.service"
TIMER="$UNIT_DIR/aegis-export.timer"

cat > "$SERVICE" <<EOF
[Unit]
Description=AEGIS verified local diagnostics export

[Service]
Type=oneshot
WorkingDirectory=$REPO_ROOT
Environment=AEGIS_REPO_ROOT=$REPO_ROOT
Environment=AEGIS_SCHEDULER_PUSH=$ENABLE_PUSH
ExecStart=/usr/bin/env bash $REPO_ROOT/scripts/aegis_scheduled_run.sh
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=6
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$REPO_ROOT %h/.local/state/aegis-scheduler
EOF

cat > "$TIMER" <<EOF
[Unit]
Description=Run AEGIS verified diagnostics periodically

[Timer]
OnBootSec=3min
OnUnitActiveSec=${INTERVAL_MINUTES}min
RandomizedDelaySec=60
Persistent=true
Unit=aegis-export.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now aegis-export.timer
systemctl --user start aegis-export.service

echo "AEGIS_SCHEDULER status=installed interval_minutes=$INTERVAL_MINUTES push=$ENABLE_PUSH"
systemctl --user --no-pager status aegis-export.timer || true
