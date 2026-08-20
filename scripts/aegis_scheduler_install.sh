#!/usr/bin/env bash
set -Eeuo pipefail

INTERVAL_MINUTES=15
ENABLE_PUSH=0
MODE=auto
DRY_RUN=0
RUN_USER=""
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: scripts/aegis_scheduler_install.sh [options]

Installs the existing guarded AEGIS scheduler. Push remains disabled by default.

Options:
  --interval-minutes N   Run interval, minimum 5 minutes (default: 15)
  --push                 Explicitly enable verified commit+push
  --mode auto|user|system
                         auto prefers user systemd, then falls back to system
  --run-user USER        User for system mode (required when root has no SUDO_USER)
  --dry-run              Print selected mode and generated units; change nothing
  -h, --help             Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --interval-minutes) INTERVAL_MINUTES="${2:?missing minutes}"; shift 2 ;;
    --push) ENABLE_PUSH=1; shift ;;
    --mode) MODE="${2:?missing mode}"; shift 2 ;;
    --run-user) RUN_USER="${2:?missing user}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 64 ;;
  esac
done

[[ "$INTERVAL_MINUTES" =~ ^[1-9][0-9]*$ ]] || { echo 'interval must be a positive integer' >&2; exit 64; }
(( INTERVAL_MINUTES >= 5 )) || { echo 'interval below 5 minutes refused' >&2; exit 64; }
[[ "$MODE" == auto || "$MODE" == user || "$MODE" == system ]] || { echo 'mode must be auto, user, or system' >&2; exit 64; }
# Keep generated unit syntax unambiguous and fail closed on unsafe specifier/path bytes.
if [[ "$REPO_ROOT" =~ [[:space:]%] ]]; then
  echo 'repository path containing whitespace or % is not supported by the hardened systemd installer' >&2
  exit 64
fi
command -v systemctl >/dev/null 2>&1 || { echo 'systemd is required for this installer' >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo 'python3 is required' >&2; exit 2; }

user_manager_available() {
  systemctl --user show-environment >/dev/null 2>&1
}

if [[ "$MODE" == auto ]]; then
  if user_manager_available; then
    MODE=user
  else
    MODE=system
  fi
fi

if [[ "$MODE" == user ]] && ! user_manager_available && [[ "$DRY_RUN" -eq 0 ]]; then
  echo 'AEGIS_SCHEDULER status=error reason=user_systemd_unavailable hint=use_--mode_system' >&2
  exit 2
fi

if [[ "$MODE" == system ]]; then
  if [[ -z "$RUN_USER" ]]; then
    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != root ]]; then
      RUN_USER="$SUDO_USER"
    elif [[ "$(id -u)" -ne 0 ]]; then
      RUN_USER="$(id -un)"
    elif [[ "$DRY_RUN" -eq 1 ]]; then
      RUN_USER="aegis-dry-run"
    else
      echo 'AEGIS_SCHEDULER status=error reason=run_user_required_for_root hint=--run-user_USER' >&2
      exit 64
    fi
  fi
  if [[ "$DRY_RUN" -eq 0 ]] && ! id "$RUN_USER" >/dev/null 2>&1; then
    echo "AEGIS_SCHEDULER status=error reason=run_user_not_found user=$RUN_USER" >&2
    exit 64
  fi
fi

SERVICE_CONTENT=""
TIMER_CONTENT=""

if [[ "$MODE" == user ]]; then
  STATE_DIR="$HOME/.local/state/aegis-scheduler"
  SERVICE_CONTENT=$(cat <<EOF
[Unit]
Description=AEGIS verified local diagnostics export

[Service]
Type=oneshot
WorkingDirectory=$REPO_ROOT
Environment=AEGIS_REPO_ROOT=$REPO_ROOT
Environment=AEGIS_SCHEDULER_PUSH=$ENABLE_PUSH
Environment=AEGIS_SCHEDULER_STATE_DIR=$STATE_DIR
ExecStart=/usr/bin/env bash $REPO_ROOT/scripts/aegis_scheduled_run.sh
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=6
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$REPO_ROOT $STATE_DIR
EOF
)
else
  RUN_GROUP="$(id -gn "$RUN_USER" 2>/dev/null || printf '%s' "$RUN_USER")"
  STATE_DIR="$REPO_ROOT/.aegis-state/scheduler"
  SERVICE_CONTENT=$(cat <<EOF
[Unit]
Description=AEGIS verified local diagnostics export
After=local-fs.target

[Service]
Type=oneshot
User=$RUN_USER
Group=$RUN_GROUP
WorkingDirectory=$REPO_ROOT
Environment=AEGIS_REPO_ROOT=$REPO_ROOT
Environment=AEGIS_SCHEDULER_PUSH=$ENABLE_PUSH
Environment=AEGIS_SCHEDULER_STATE_DIR=$STATE_DIR
ExecStart=/usr/bin/env bash $REPO_ROOT/scripts/aegis_scheduled_run.sh
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=6
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=false
ReadWritePaths=$REPO_ROOT
LockPersonality=true
RestrictRealtime=true
RestrictSUIDSGID=true
UMask=0077
EOF
)
fi

TIMER_CONTENT=$(cat <<EOF
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
)

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "AEGIS_SCHEDULER status=dry_run mode=$MODE interval_minutes=$INTERVAL_MINUTES push=$ENABLE_PUSH"
  echo '--- aegis-export.service ---'
  printf '%s\n' "$SERVICE_CONTENT"
  echo '--- aegis-export.timer ---'
  printf '%s\n' "$TIMER_CONTENT"
  exit 0
fi

if [[ "$MODE" == user ]]; then
  UNIT_DIR="$HOME/.config/systemd/user"
  mkdir -p "$UNIT_DIR" "$STATE_DIR"
  printf '%s\n' "$SERVICE_CONTENT" > "$UNIT_DIR/aegis-export.service"
  printf '%s\n' "$TIMER_CONTENT" > "$UNIT_DIR/aegis-export.timer"
  systemctl --user daemon-reload
  systemctl --user enable --now aegis-export.timer
  systemctl --user start aegis-export.service
  echo "AEGIS_SCHEDULER status=installed mode=user interval_minutes=$INTERVAL_MINUTES push=$ENABLE_PUSH"
  systemctl --user --no-pager status aegis-export.timer || true
  exit 0
fi

# System fallback: privilege is used only to install/enable the timer; the AEGIS
# job itself runs as RUN_USER and retains the existing guarded scheduler logic.
if [[ "$(id -u)" -eq 0 ]]; then
  PRIV=()
else
  command -v sudo >/dev/null 2>&1 || { echo 'AEGIS_SCHEDULER status=error reason=sudo_missing_for_system_mode' >&2; exit 2; }
  PRIV=(sudo)
fi

mkdir -p "$STATE_DIR"
if [[ "$(id -u)" -eq 0 ]]; then
  chown -R "$RUN_USER:$RUN_GROUP" "$STATE_DIR"
else
  "${PRIV[@]}" chown -R "$RUN_USER:$RUN_GROUP" "$STATE_DIR"
fi
printf '%s\n' "$SERVICE_CONTENT" | "${PRIV[@]}" tee /etc/systemd/system/aegis-export.service >/dev/null
printf '%s\n' "$TIMER_CONTENT" | "${PRIV[@]}" tee /etc/systemd/system/aegis-export.timer >/dev/null
"${PRIV[@]}" systemctl daemon-reload
"${PRIV[@]}" systemctl enable --now aegis-export.timer
"${PRIV[@]}" systemctl start aegis-export.service

echo "AEGIS_SCHEDULER status=installed mode=system run_user=$RUN_USER interval_minutes=$INTERVAL_MINUTES push=$ENABLE_PUSH"
"${PRIV[@]}" systemctl --no-pager status aegis-export.timer || true
