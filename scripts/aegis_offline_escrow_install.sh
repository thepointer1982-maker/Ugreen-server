#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${AEGIS_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
UNIT_DIR="$HOME/.config/systemd/user"
ESCROW_ROOT="${AEGIS_ESCROW_ROOT:-$HOME/.local/share/aegis-escrow}"

for cmd in python3 git systemctl; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "$cmd missing" >&2
    exit 3
  }
done

systemctl --user show-environment >/dev/null 2>&1 || {
  echo "AEGIS_ESCROW status=blocked reason=user-systemd-unavailable" >&2
  exit 4
}

mkdir -p "$UNIT_DIR" "$ESCROW_ROOT"
chmod 700 "$ESCROW_ROOT"

cat > "$UNIT_DIR/aegis-offline-escrow.service" <<EOF
[Unit]
Description=AEGIS local offline source escrow
After=local-fs.target

[Service]
Type=oneshot
WorkingDirectory=$REPO_ROOT
Environment=AEGIS_REPO_ROOT=$REPO_ROOT
Environment=AEGIS_ESCROW_ROOT=$ESCROW_ROOT
Environment=AEGIS_ESCROW_KEEP=8
ExecStart=/usr/bin/env python3 $REPO_ROOT/scripts/aegis_offline_escrow.py create
Nice=15
IOSchedulingClass=best-effort
IOSchedulingPriority=7
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$ESCROW_ROOT
RestrictAddressFamilies=AF_UNIX
LockPersonality=true
RestrictRealtime=true
RestrictSUIDSGID=true
UMask=0077
EOF

cat > "$UNIT_DIR/aegis-offline-escrow.timer" <<'EOF'
[Unit]
Description=Create AEGIS local offline source escrow weekly

[Timer]
OnBootSec=5min
OnUnitActiveSec=7d
RandomizedDelaySec=30min
Persistent=true
Unit=aegis-offline-escrow.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now aegis-offline-escrow.timer

echo "AEGIS_ESCROW status=scheduled root=$ESCROW_ROOT"
