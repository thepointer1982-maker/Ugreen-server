#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${AEGIS_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
UNIT_DIR="$HOME/.config/systemd/user"
STATE_DIR="${AEGIS_DOCKER_EFFICIENCY_STATE_DIR:-$HOME/.local/state/aegis-docker-efficiency}"
COMPOSE_ROOT="${AEGIS_DOCKER_COMPOSE_ROOT:-/volume1/docker}"

for cmd in python3 systemctl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd missing" >&2; exit 3; }
done

mkdir -p "$UNIT_DIR" "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true

cat > "$UNIT_DIR/aegis-docker-efficiency.service" <<EOF
[Unit]
Description=AEGIS incremental read-only Docker efficiency guardian
After=docker.service
Wants=docker.service

[Service]
Type=oneshot
WorkingDirectory=$REPO_ROOT
Environment=AEGIS_REPO_ROOT=$REPO_ROOT
Environment=AEGIS_DOCKER_EFFICIENCY_STATE_DIR=$STATE_DIR
Environment=AEGIS_DOCKER_COMPOSE_ROOT=$COMPOSE_ROOT
Environment=AEGIS_DOCKER_FULL_INSPECT_SECONDS=900
ExecStart=/usr/bin/env python3 $REPO_ROOT/scripts/aegis_docker_efficiency.py
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$STATE_DIR
LockPersonality=true
RestrictRealtime=true
RestrictSUIDSGID=true
UMask=0077
EOF

cat > "$UNIT_DIR/aegis-docker-efficiency.timer" <<'EOF'
[Unit]
Description=Run AEGIS Docker fast health check every 2 minutes

[Timer]
OnBootSec=25s
OnUnitActiveSec=2min
AccuracySec=10s
Persistent=true
Unit=aegis-docker-efficiency.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now aegis-docker-efficiency.timer
systemctl --user start aegis-docker-efficiency.service || true

timer_active=0
service_state="unknown"
if systemctl --user is-active --quiet aegis-docker-efficiency.timer; then
  timer_active=1
fi
service_state="$(systemctl --user is-active aegis-docker-efficiency.service 2>/dev/null || true)"

printf 'AEGIS_DOCKER_EFFICIENCY timer_active=%s service_state=%s state=%s/status.json\n' \
  "$timer_active" "$service_state" "$STATE_DIR"

[[ "$timer_active" -eq 1 ]]
