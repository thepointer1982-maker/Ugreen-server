#!/usr/bin/env bash
set -euo pipefail

MODE="user"
ENABLE=1
START=1
REPAIR=0
PUSH=0
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: aegis_deploy_local.sh [options]

Options:
  --repo-root PATH    Ugreen-server repository root
  --mode user|system  Install scheduler as user (default) or system service
  --no-enable         Do not enable timer
  --no-start          Do not start timer after install
  --repair            Allow Guardian's narrow reversible repair allowlist
  --push              Explicitly allow guarded GitHub artifact push during first real cycle
  -h, --help          Show help

This script is local-only. It does not open router ports, expose services,
or read/store passwords.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)
      [[ $# -ge 2 ]] || { echo "Missing value for --repo-root" >&2; exit 2; }
      REPO_ROOT="$2"; shift 2 ;;
    --mode)
      [[ $# -ge 2 ]] || { echo "Missing value for --mode" >&2; exit 2; }
      MODE="$2"; shift 2 ;;
    --no-enable) ENABLE=0; shift ;;
    --no-start) START=0; shift ;;
    --repair) REPAIR=1; shift ;;
    --push) PUSH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "$MODE" == "user" || "$MODE" == "system" ]] || {
  echo "Invalid --mode: $MODE" >&2
  exit 2
}

REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"
cd "$REPO_ROOT"

required=(
  scripts/aegis_nas_bootstrap.sh
  scripts/aegis_scheduler_install.sh
  scripts/aegis_real_cycle.py
  scripts/aegis_real_status.py
)
for path in "${required[@]}"; do
  [[ -f "$path" ]] || { echo "Missing required file: $path" >&2; exit 3; }
done

echo "=== AEGIS LOCAL DEPLOY ==="
echo "repo=$REPO_ROOT"
echo "mode=$MODE"
echo "push=$PUSH"
echo "repair=$REPAIR"

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  head="$(git rev-parse HEAD 2>/dev/null || true)"
  echo "branch=$branch"
  echo "head=$head"
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "Blocked: tracked worktree changes present." >&2
    exit 4
  fi
fi

bash scripts/aegis_nas_bootstrap.sh --repo-root "$REPO_ROOT"

install_args=(--mode "$MODE" --repo-root "$REPO_ROOT")
[[ "$ENABLE" -eq 0 ]] && install_args+=(--no-enable)
[[ "$START" -eq 0 ]] && install_args+=(--no-start)
bash scripts/aegis_scheduler_install.sh "${install_args[@]}"

cycle_args=(--repo-root "$REPO_ROOT")
[[ "$REPAIR" -eq 1 ]] && cycle_args+=(--repair)
[[ "$PUSH" -eq 1 ]] && cycle_args+=(--push)
python3 scripts/aegis_real_cycle.py "${cycle_args[@]}"

status_file="${AEGIS_REAL_STATUS_FILE:-$HOME/.local/state/aegis-real-status/latest.json}"
if [[ -s "$status_file" ]]; then
  echo "=== AEGIS REAL STATUS ==="
  python3 - "$status_file" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
d = json.loads(p.read_text(encoding="utf-8"))
print(json.dumps({
    "health": d.get("health"),
    "blockers": d.get("blockers"),
    "real_cycle": d.get("real_cycle"),
    "guardian": d.get("guardian"),
    "scheduler": d.get("scheduler"),
    "ollama": d.get("ollama"),
    "learning": d.get("learning"),
}, indent=2, ensure_ascii=False))
PY
else
  echo "Blocked: real status file was not produced: $status_file" >&2
  exit 5
fi
