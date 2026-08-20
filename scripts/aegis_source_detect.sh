#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only AEGIS source detector for UGREEN/Linux hosts.
# It searches a bounded set of likely roots for reports/latest.json,
# validates the baseline score schema, and optionally invokes the existing
# export collector with the selected source root.

DO_EXPORT=0
REPO_ROOT="$(pwd)"
MAX_DEPTH=6

usage() {
  cat <<'EOF'
Usage: scripts/aegis_source_detect.sh [--export] [--repo-root PATH] [--max-depth N]

Default mode is read-only and prints the unique valid AEGIS source root.
If AEGIS_SOURCE_ROOT is set, it is a strict override: only that path is checked.
--export      Run scripts/aegis_export_collect.sh after a unique valid source is found.
--repo-root   Repository root used by --export (default: current directory).
--max-depth   Maximum bounded search depth below candidate roots (default: 6).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --export) DO_EXPORT=1; shift ;;
    --repo-root) REPO_ROOT="${2:?missing path}"; shift 2 ;;
    --max-depth) MAX_DEPTH="${2:?missing depth}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 64 ;;
  esac
done

[[ "$MAX_DEPTH" =~ ^[1-9][0-9]*$ ]] || { echo "max-depth must be a positive integer" >&2; exit 64; }
command -v python3 >/dev/null 2>&1 || { echo "AEGIS_SOURCE_DETECT status=error reason=python3_missing" >&2; exit 2; }

validate_root() {
  local root="$1"
  local latest="$root/reports/latest.json"
  [[ -f "$latest" ]] || return 1
  python3 - "$latest" <<'PY'
import json, sys
p = sys.argv[1]
try:
    with open(p, encoding='utf-8') as f:
        d = json.load(f)
except Exception:
    raise SystemExit(1)
required = ('generated_at', 'network_score', 'devices')
if any(k not in d for k in required):
    raise SystemExit(1)
score = d['network_score']
if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= float(score) <= 100:
    raise SystemExit(1)
if not isinstance(d['devices'], list):
    raise SystemExit(1)
PY
}

CANDIDATE_ROOTS=()

# Explicit root is a strict fail-closed override. Never silently fall back to
# another AEGIS tree when the operator selected a source explicitly.
if [[ -n "${AEGIS_SOURCE_ROOT:-}" ]]; then
  if ! validate_root "$AEGIS_SOURCE_ROOT"; then
    echo "AEGIS_SOURCE_DETECT status=invalid_explicit_source" >&2
    printf 'source_root=%q\n' "$AEGIS_SOURCE_ROOT" >&2
    exit 4
  fi
  CANDIDATE_ROOTS=("$AEGIS_SOURCE_ROOT")
else
  for root in \
    /var/lib/aegis-device-ai \
    /var/lib/aegis \
    /opt/aegis \
    /srv/aegis \
    /volume1/aegis \
    /volume1/docker/aegis \
    /volume2/aegis \
    "$HOME/aegis" \
    "$HOME/AEGIS-Device-AI"; do
    [[ -e "$root" ]] && CANDIDATE_ROOTS+=("$root")
  done

  # Bounded discovery only. No network calls and no writes.
  for parent in /var/lib /opt /srv /volume1 /volume2 "$HOME"; do
    [[ -d "$parent" ]] || continue
    while IFS= read -r -d '' latest; do
      CANDIDATE_ROOTS+=("${latest%/reports/latest.json}")
    done < <(find "$parent" -maxdepth "$MAX_DEPTH" -type f -path '*/reports/latest.json' -print0 2>/dev/null || true)
  done
fi

# De-duplicate while preserving order.
UNIQUE=()
declare -A SEEN=()
for root in "${CANDIDATE_ROOTS[@]}"; do
  [[ -n "$root" ]] || continue
  if [[ -z "${SEEN[$root]:-}" ]]; then
    UNIQUE+=("$root")
    SEEN[$root]=1
  fi
done

VALID=()
for root in "${UNIQUE[@]}"; do
  if validate_root "$root"; then
    VALID+=("$root")
  fi
done

if [[ ${#VALID[@]} -eq 0 ]]; then
  echo "AEGIS_SOURCE_DETECT status=no_valid_source"
  echo "Checked only bounded local paths; no network access was used."
  exit 3
fi

if [[ ${#VALID[@]} -gt 1 ]]; then
  echo "AEGIS_SOURCE_DETECT status=ambiguous count=${#VALID[@]}"
  printf 'candidate=%q\n' "${VALID[@]}"
  echo "Set AEGIS_SOURCE_ROOT explicitly and run again." >&2
  exit 5
fi

SOURCE_ROOT="${VALID[0]}"
echo "AEGIS_SOURCE_DETECT status=ready"
printf 'source_root=%q\n' "$SOURCE_ROOT"
for rel in reports/latest.json reports/deepdiag.json data/history.jsonl reports/dashboard.html; do
  if [[ -f "$SOURCE_ROOT/$rel" ]]; then
    printf 'present=%s\n' "$rel"
  else
    printf 'missing=%s\n' "$rel"
  fi
done

if [[ "$DO_EXPORT" -eq 1 ]]; then
  EXPORTER="$REPO_ROOT/scripts/aegis_export_collect.sh"
  [[ -f "$EXPORTER" ]] || { echo "Exporter not found: $EXPORTER" >&2; exit 6; }
  AEGIS_SOURCE_ROOT="$SOURCE_ROOT" bash "$EXPORTER" "$REPO_ROOT"
fi
