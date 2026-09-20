#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${AEGIS_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
UNIT_DIR="$HOME/.config/systemd/user"
STATE_DIR="${AEGIS_LIFECYCLE_STATE_DIR:-$HOME/.local/state/aegis-lifecycle}"
MANIFEST="${AEGIS_LIFECYCLE_MANIFEST:-$REPO_ROOT/config/lifecycle/aegis-services.json}"

for cmd in python3 systemctl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd missing" >&2; exit 3; }
done

[[ -s "$MANIFEST" ]] || { echo "lifecycle manifest missing: $MANIFEST" >&2; exit 4; }
systemctl --user show-environment >/dev/null 2>&1 || {
  echo "AEGIS_LIFECYCLE status=blocked reason=user-systemd-unavailable" >&2
  exit 5
}

mkdir -p "$UNIT_DIR" "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true

cat > "$UNIT_DIR/aegis-lifecycle.service" <<EOF
[Unit]
Description=AEGIS read-only service lifecycle guardian
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$REPO_ROOT
Environment=AEGIS_REPO_ROOT=$REPO_ROOT
Environment=AEGIS_LIFECYCLE_MANIFEST=$MANIFEST
Environment=AEGIS_LIFECYCLE_STATE_DIR=$STATE_DIR
ExecStart=/usr/bin/env python3 $REPO_ROOT/scripts/aegis_service_lifecycle.py
Nice=15
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

cat > "$UNIT_DIR/aegis-lifecycle.timer" <<'EOF'
[Unit]
Description=Run AEGIS service lifecycle audit daily

[Timer]
OnActiveSec=2min
OnUnitActiveSec=1d
RandomizedDelaySec=10min
Persistent=true
Unit=aegis-lifecycle.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now aegis-lifecycle.timer

active=0
if systemctl --user is-active --quiet aegis-lifecycle.timer; then
  active=1
fi

printf 'AEGIS_LIFECYCLE timer_active=%s state=%s/status.json\n' "$active" "$STATE_DIR"
[[ "$active" -eq 1 ]]
