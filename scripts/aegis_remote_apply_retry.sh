#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAX_ATTEMPTS="${AEGIS_REMOTE_MAX_ATTEMPTS:-8}"
BASE_DELAY="${AEGIS_REMOTE_BASE_DELAY:-10}"
MAX_DELAY="${AEGIS_REMOTE_MAX_DELAY:-120}"
MODE="${AEGIS_DEPLOY_MODE:-user}"
REPAIR="${AEGIS_REMOTE_REPAIR:-0}"
FULL_BOOTSTRAP="${AEGIS_REMOTE_FULL_BOOTSTRAP:-1}"
PRIMARY_REPO="${AEGIS_PRIMARY_REPO_ROOT:-$HOME/aegis/Ugreen-server}"

usage() {
  cat <<'EOF'
Usage: aegis_remote_apply_retry.sh [--repo-root PATH] [--attempts N] [--repair] [--no-full-bootstrap]

By default, first applies the exact trusted checkout as the persistent
zero-cost AEGIS control plane, then runs local deploy/real-cycle repeatedly
until signed real-status reports health=healthy or a hard blocker is reached.

--no-full-bootstrap keeps the existing repo in place and only exercises retry logic.

No passwords are read or stored by this script.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)
      [[ $# -ge 2 ]] || { echo "Missing --repo-root value" >&2; exit 2; }
      REPO_ROOT="$2"; shift 2 ;;
    --attempts)
      [[ $# -ge 2 ]] || { echo "Missing --attempts value" >&2; exit 2; }
      MAX_ATTEMPTS="$2"; shift 2 ;;
    --repair)
      REPAIR=1; shift ;;
    --no-full-bootstrap)
      FULL_BOOTSTRAP=0; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2 ;;
  esac
done

[[ "$MAX_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || {
  echo "Invalid attempt count: $MAX_ATTEMPTS" >&2
  exit 2
}

[[ "$FULL_BOOTSTRAP" == "0" || "$FULL_BOOTSTRAP" == "1" ]] || {
  echo "AEGIS_REMOTE_FULL_BOOTSTRAP must be 0 or 1" >&2
  exit 2
}

REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"
cd "$REPO_ROOT"

required=(
  scripts/aegis_deploy_local.sh
  scripts/aegis_real_cycle.py
  scripts/aegis_real_status.py
)
if [[ "$FULL_BOOTSTRAP" == "1" ]]; then
  required+=(scripts/aegis_zero_cost_bootstrap.sh)
fi
for path in "${required[@]}"; do
  [[ -f "$path" ]] || { echo "Missing required file: $path" >&2; exit 3; }
done

STATUS_FILE="${AEGIS_REAL_STATUS_FILE:-$HOME/.local/state/aegis-real-status/latest.json}"
LOG_DIR="${AEGIS_REMOTE_LOG_DIR:-$HOME/.local/state/aegis-remote-apply}"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/retry.log"

log() {
  printf '%s %s\n' "$(date -Is)" "$*" | tee -a "$LOG_FILE"
}

if [[ "$FULL_BOOTSTRAP" == "1" ]]; then
  TRUSTED_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || true)"
  [[ "$TRUSTED_SHA" =~ ^[0-9a-f]{40}$ ]] || {
    log "stop reason=invalid-trusted-head repo=$REPO_ROOT"
    exit 22
  }

  log "bootstrap_start trusted_sha=$TRUSTED_SHA primary_repo=$PRIMARY_REPO"
  set +e
  AEGIS_TRUSTED_SHA="$TRUSTED_SHA"   AEGIS_DEST="$PRIMARY_REPO"   AEGIS_BRANCH="aegis/resume-pre-lenovo-20260920"   AEGIS_BOOTSTRAP_FROM_PULL_CONTROL=1   AEGIS_PULL_CONTROL_NO_START=1   AEGIS_ALLOW_CLOUD_CODEX=0   bash "$REPO_ROOT/scripts/aegis_zero_cost_bootstrap.sh" >>"$LOG_FILE" 2>&1
  bootstrap_rc=$?
  set -e
  if [[ "$bootstrap_rc" -ne 0 ]]; then
    log "stop reason=full-bootstrap-failed rc=$bootstrap_rc"
    exit 22
  fi

  REPO_ROOT="$(cd "$PRIMARY_REPO" && pwd)"
  cd "$REPO_ROOT"
  for path in scripts/aegis_deploy_local.sh scripts/aegis_real_cycle.py scripts/aegis_real_status.py; do
    [[ -f "$path" ]] || {
      log "stop reason=primary-repo-missing path=$path"
      exit 23
    }
  done
  log "bootstrap_complete repo=$REPO_ROOT trusted_sha=$TRUSTED_SHA"
fi

read_health() {
  python3 - "$STATUS_FILE" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.is_file():
    print("missing")
    raise SystemExit(0)
try:
    d = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    print("unreadable")
    raise SystemExit(0)
print(d.get("health") or "unknown")
PY
}

read_blockers() {
  python3 - "$STATUS_FILE" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.is_file():
    print("[]")
    raise SystemExit(0)
try:
    d = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    print('["status-unreadable"]')
    raise SystemExit(0)
print(json.dumps(d.get("blockers") or [], ensure_ascii=False))
PY
}

hard_blocker() {
  python3 - "$STATUS_FILE" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
hard = {"ai-provenance-invalid", "real-cycle-status-missing"}
if not p.is_file():
    raise SystemExit(1)
try:
    d = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
blockers = set(d.get("blockers") or [])
raise SystemExit(0 if blockers & hard else 1)
PY
}

delay_for() {
  local attempt="$1"
  local delay=$(( BASE_DELAY * (2 ** (attempt - 1)) ))
  (( delay > MAX_DELAY )) && delay="$MAX_DELAY"
  printf '%s' "$delay"
}

log "start repo=$REPO_ROOT attempts=$MAX_ATTEMPTS mode=$MODE repair=$REPAIR full_bootstrap=$FULL_BOOTSTRAP"

for ((attempt=1; attempt<=MAX_ATTEMPTS; attempt++)); do
  log "attempt=$attempt/$MAX_ATTEMPTS"

  args=(--repo-root "$REPO_ROOT" --mode "$MODE")
  [[ "$REPAIR" == "1" ]] && args+=(--repair)

  set +e
  bash scripts/aegis_deploy_local.sh "${args[@]}" >>"$LOG_FILE" 2>&1
  rc=$?
  set -e

  health="$(read_health)"
  blockers="$(read_blockers)"
  log "attempt=$attempt rc=$rc health=$health blockers=$blockers"

  if [[ "$health" == "healthy" ]]; then
    log "success health=healthy"
    python3 scripts/aegis_real_status.py --repo-root "$REPO_ROOT"
    exit 0
  fi

  if hard_blocker; then
    log "stop reason=hard-blocker blockers=$blockers"
    exit 20
  fi

  if (( attempt == MAX_ATTEMPTS )); then
    log "stop reason=max-attempts health=$health blockers=$blockers"
    exit 21
  fi

  sleep_for="$(delay_for "$attempt")"
  log "retry_in=${sleep_for}s"
  sleep "$sleep_for"
done

exit 21
