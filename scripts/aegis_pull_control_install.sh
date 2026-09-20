#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${AEGIS_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
UNIT_DIR="$HOME/.config/systemd/user"
STATE_DIR="$HOME/.local/state/aegis-pull-control"

command -v python3 >/dev/null || { echo "python3 missing" >&2; exit 3; }
command -v git >/dev/null || { echo "git missing" >&2; exit 3; }
command -v systemctl >/dev/null || { echo "systemctl missing" >&2; exit 3; }

mkdir -p "$UNIT_DIR" "$STATE_DIR"
chmod 700 "$STATE_DIR"

cat > "$UNIT_DIR/aegis-pull-control.service" <<EOF
[Unit]
Description=AEGIS outbound pull control
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$REPO_ROOT
ExecStart=/usr/bin/env python3 $REPO_ROOT/scripts/aegis_pull_control.py --repo-root $REPO_ROOT
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=%h/.local/state/aegis-pull-control %h/.local/share/aegis-pull-control
EOF

cat > "$UNIT_DIR/aegis-pull-control.timer" <<'EOF'
[Unit]
Description=Run AEGIS outbound pull control every 2 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
AccuracySec=15s
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now aegis-pull-control.timer
systemctl --user start aegis-pull-control.service
systemctl --user --no-pager status aegis-pull-control.timer || true
echo "state=$STATE_DIR/state.json"
