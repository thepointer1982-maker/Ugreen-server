#!/usr/bin/env bash
set -Eeuo pipefail

# Safe periodic wrapper around aegis_nas_run_once.sh.
# Local-only by default. Set AEGIS_SCHEDULER_PUSH=1 only when GitHub export is
# explicitly desired. Uses a lock and exponential failure backoff.

REPO_ROOT="${AEGIS_REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
STATE_DIR="${AEGIS_SCHEDULER_STATE_DIR:-$HOME/.local/state/aegis-scheduler}"
LOCK_DIR="$STATE_DIR/run.lock"
STATE_FILE="$STATE_DIR/state.env"
LOG_FILE="$STATE_DIR/scheduler.log"
mkdir -p "$STATE_DIR"

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG_FILE"
}

# Portable mkdir lock. Stale locks older than 2 hours are removed.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if [[ -d "$LOCK_DIR" ]]; then
    now="$(date +%s)"
    if stat -c %Y "$LOCK_DIR" >/dev/null 2>&1; then
      mtime="$(stat -c %Y "$LOCK_DIR")"
    else
      mtime="$(stat -f %m "$LOCK_DIR" 2>/dev/null || echo "$now")"
    fi
    if (( now - mtime > 7200 )); then
      rm -rf -- "$LOCK_DIR"
      mkdir "$LOCK_DIR" || { log 'status=skip reason=lock_busy'; exit 0; }
      log 'status=warning reason=stale_lock_recovered'
    else
      log 'status=skip reason=lock_busy'
      exit 0
    fi
  fi
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

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
