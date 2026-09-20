#!/usr/bin/env bash
set -euo pipefail
umask 077

REPO_ROOT="${AEGIS_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
UNIT_DIR="$HOME/.config/systemd/user"
BACKUP_ROOT="${AEGIS_RUNTIME_BACKUP_ROOT:-$HOME/aegis-backups/runtime}"
KEY_DIR="${AEGIS_RUNTIME_BACKUP_KEY_DIR:-$HOME/.config/aegis-runtime-backup}"
STATE_DIR="${AEGIS_RUNTIME_BACKUP_STATE_DIR:-$HOME/.local/state/aegis-runtime-backup}"
CONFIG="${AEGIS_RUNTIME_BACKUP_CONFIG:-$REPO_ROOT/config/backup/aegis-runtime-backup.json}"
ENC_KEY="$KEY_DIR/enc.key"
MAC_KEY="$KEY_DIR/mac.key"

for cmd in python3 openssl systemctl; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "$cmd missing" >&2
    exit 3
  }
done

[[ -s "$CONFIG" ]] || {
  echo "runtime backup config missing: $CONFIG" >&2
  exit 4
}

systemctl --user show-environment >/dev/null 2>&1 || {
  echo "AEGIS_RUNTIME_BACKUP status=blocked reason=user-systemd-unavailable" >&2
  exit 5
}

mkdir -p "$UNIT_DIR" "$BACKUP_ROOT" "$KEY_DIR" "$STATE_DIR"
chmod 700 "$BACKUP_ROOT" "$KEY_DIR" "$STATE_DIR"

generate_key() {
  local target="$1"
  if [[ ! -s "$target" ]]; then
    openssl rand -hex 32 > "$target"
  fi
  chmod 600 "$target"
  local raw
  raw="$(tr -d '\r\n' < "$target")"
  [[ "$raw" =~ ^[0-9a-fA-F]{64}$ ]] || {
    echo "invalid key material: $target" >&2
    exit 6
  }
}

generate_key "$ENC_KEY"
generate_key "$MAC_KEY"

if [[ "$BACKUP_ROOT" == "$KEY_DIR"* || "$KEY_DIR" == "$BACKUP_ROOT"* ]]; then
  echo "backup and key directories must be separate" >&2
  exit 7
fi

cat > "$UNIT_DIR/aegis-runtime-backup.service" <<EOF
[Unit]
Description=AEGIS encrypted local runtime backup
After=local-fs.target

[Service]
Type=oneshot
WorkingDirectory=$REPO_ROOT
Environment=AEGIS_REPO_ROOT=$REPO_ROOT
Environment=AEGIS_RUNTIME_BACKUP_CONFIG=$CONFIG
Environment=AEGIS_RUNTIME_BACKUP_ROOT=$BACKUP_ROOT
Environment=AEGIS_RUNTIME_BACKUP_KEY_DIR=$KEY_DIR
Environment=AEGIS_RUNTIME_BACKUP_STATE_DIR=$STATE_DIR
ExecStart=/usr/bin/env python3 $REPO_ROOT/scripts/aegis_runtime_backup.py create
Nice=15
IOSchedulingClass=best-effort
IOSchedulingPriority=7
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$BACKUP_ROOT $STATE_DIR
ReadOnlyPaths=$KEY_DIR
RestrictAddressFamilies=AF_UNIX
LockPersonality=true
RestrictRealtime=true
RestrictSUIDSGID=true
UMask=0077
EOF

cat > "$UNIT_DIR/aegis-runtime-backup.timer" <<'EOF'
[Unit]
Description=Run AEGIS encrypted local runtime backup daily

[Timer]
OnBootSec=10min
OnUnitActiveSec=1d
RandomizedDelaySec=20min
Persistent=true
Unit=aegis-runtime-backup.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now aegis-runtime-backup.timer

fingerprint="$(
  AEGIS_REPO_ROOT="$REPO_ROOT"   AEGIS_RUNTIME_BACKUP_CONFIG="$CONFIG"   AEGIS_RUNTIME_BACKUP_ROOT="$BACKUP_ROOT"   AEGIS_RUNTIME_BACKUP_KEY_DIR="$KEY_DIR"   AEGIS_RUNTIME_BACKUP_STATE_DIR="$STATE_DIR"   python3 - <<'PY'
import importlib.util
import os
from pathlib import Path

path = Path(os.environ["AEGIS_REPO_ROOT"]) / "scripts" / "aegis_runtime_backup.py"
spec = importlib.util.spec_from_file_location("aegis_runtime_backup", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.key_fingerprint())
PY
)"

echo "AEGIS_RUNTIME_BACKUP status=scheduled"
echo "backup_root=$BACKUP_ROOT"
echo "key_dir=$KEY_DIR"
echo "key_fingerprint=$fingerprint"
echo "IMPORTANT: copy $KEY_DIR to a separate offline medium; the keys are intentionally not included in runtime backups."
