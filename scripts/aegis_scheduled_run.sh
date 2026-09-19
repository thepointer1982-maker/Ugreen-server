#!/usr/bin/env bash
set -Eeuo pipefail

# Safe periodic wrapper around aegis_nas_run_once.sh.
# Local-only by default. Set AEGIS_SCHEDULER_PUSH=1 only when GitHub export is
# explicitly desired. Uses a lock and exponential failure backoff.

REPO_ROOT="${AEGIS_REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
STATE_DIR="${AEGIS_SCHEDULER_STATE_DIR:-$HOME/.local/state/aegis-scheduler}"
LOCK_DIR="$STATE_DIR/run.lock"
LOCK_OWNER="$LOCK_DIR/owner"
STATE_FILE="$STATE_DIR/state.env"
LOG_FILE="$STATE_DIR/scheduler.log"
BOOT_ID="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || printf '%s' unknown)"
mkdir -p "$STATE_DIR"

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG_FILE"
}

# PID + boot-id lock. A live owner on the current boot is never treated as
# stale, even when the run is long. Unknown/corrupt owners fail closed unless
# the lock is older than two hours.
acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf 'pid=%s\nboot_id=%s\n' "$" "$BOOT_ID" > "$LOCK_OWNER"
    chmod 600 "$LOCK_OWNER" 2>/dev/null || true
    return 0
  fi

  if [[ ! -d "$LOCK_DIR" || -L "$LOCK_DIR" ]]; then
    log 'status=error reason=lock_acquisition_failed'
    return 13
  fi

  owner_pid=""
  owner_boot=""
  if [[ -f "$LOCK_OWNER" && ! -L "$LOCK_OWNER" ]]; then
    owner_pid="$(sed -n 's/^pid=//p' "$LOCK_OWNER" | head -n1)"
    owner_boot="$(sed -n 's/^boot_id=//p' "$LOCK_OWNER" | head -n1)"
  fi
  [[ "$owner_pid" =~ ^[0-9]+$ ]] || owner_pid=""

  if [[ -n "$owner_pid" && "$owner_boot" == "$BOOT_ID" ]] && kill -0 "$owner_pid" 2>/dev/null; then
    log "status=skip reason=lock_busy owner_pid=$owner_pid"
    return 1
  fi

  now="$(date +%s)"
  if stat -c %Y "$LOCK_DIR" >/dev/null 2>&1; then
    mtime="$(stat -c %Y "$LOCK_DIR")"
  else
    mtime="$(stat -f %m "$LOCK_DIR" 2>/dev/null || echo "$now")"
  fi

  if (( now - mtime <= 7200 )); then
    log 'status=skip reason=lock_owner_unverified_not_old'
    return 1
  fi

  rm -f -- "$LOCK_OWNER" 2>/dev/null || true
  rmdir "$LOCK_DIR" 2>/dev/null || {
    log 'status=skip reason=stale_lock_not_empty_or_changed'
    return 1
  }
  mkdir "$LOCK_DIR" || {
    log 'status=skip reason=lock_raced'
    return 1
  }
  printf 'pid=%s\nboot_id=%s\n' "$" "$BOOT_ID" > "$LOCK_OWNER"
  chmod 600 "$LOCK_OWNER" 2>/dev/null || true
  log 'status=warning reason=stale_lock_recovered'
  return 0
}

if ! acquire_lock; then
  rc=$?
  [[ "$rc" -eq 13 ]] && exit 13
  exit 0
fi
trap 'rm -f "$LOCK_OWNER"; rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

failures=0
next_allowed=0
if [[ -f "$STATE_FILE" ]]; then
  while IFS='=' read -r k v; do
    case "$k" in
      failures) [[ "$v" =~ ^[0-9]+$ ]] && failures="$v" ;;
      next_allowed) [[ "$v" =~ ^[0-9]+$ ]] && next_allowed="$v" ;;
    esac
  done < "$STATE_FILE"
fi

now="$(date +%s)"
if (( now < next_allowed )); then
  log "status=skip reason=backoff next_allowed=$next_allowed failures=$failures"
  exit 0
fi

args=(--repo-root "$REPO_ROOT")
if [[ "${AEGIS_SCHEDULER_PUSH:-0}" == "1" ]]; then
  args+=(--push)
fi

log "status=start push=${AEGIS_SCHEDULER_PUSH:-0}"
set +e
bash "$REPO_ROOT/scripts/aegis_nas_run_once.sh" "${args[@]}" >>"$LOG_FILE" 2>&1
rc=$?
set -e

if (( rc == 0 )); then
  printf 'failures=0\nnext_allowed=0\nlast_success=%s\n' "$now" > "$STATE_FILE"
  log 'status=success'
  exit 0
fi

failures=$((failures + 1))
# 15m, 30m, 60m, 120m, capped at 6h.
delay=$((900 * (1 << (failures > 5 ? 5 : failures - 1))))
(( delay > 21600 )) && delay=21600
next_allowed=$((now + delay))
printf 'failures=%s\nnext_allowed=%s\nlast_failure=%s\nlast_rc=%s\n' "$failures" "$next_allowed" "$now" "$rc" > "$STATE_FILE"
log "status=failed rc=$rc failures=$failures backoff_seconds=$delay"
exit "$rc"
