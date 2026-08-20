#!/usr/bin/env bash
set -Eeuo pipefail

if [[ -n "${AEGIS_SOURCE_ROOT:-}" ]]; then
  SOURCE_ROOT="$AEGIS_SOURCE_ROOT"
elif [[ "$(uname -s)" == "Darwin" ]]; then
  SOURCE_ROOT="$HOME/Library/Application Support/AEGIS-Device-AI"
else
  SOURCE_ROOT="/var/lib/aegis-device-ai"
fi

REPO_ROOT="${1:-$(pwd)}"
SCORES_DIR="$REPO_ROOT/scores"
DASH_DIR="$REPO_ROOT/dashboard"
mkdir -p "$SCORES_DIR" "$DASH_DIR"

copy_if_present() {
  local src="$1" dst="$2"
  if [[ -f "$src" ]]; then
    cp "$src" "$dst"
  fi
}

copy_if_present "$SOURCE_ROOT/reports/latest.json" "$SCORES_DIR/latest.json"
copy_if_present "$SOURCE_ROOT/reports/deepdiag.json" "$SCORES_DIR/deepdiag.json"
copy_if_present "$SOURCE_ROOT/data/history.jsonl" "$SCORES_DIR/history.jsonl"
copy_if_present "$SOURCE_ROOT/reports/dashboard.html" "$DASH_DIR/index.html"
copy_if_present "$SOURCE_ROOT/reports/latest.json" "$DASH_DIR/status.json"

# Refuse obvious secret material before hashing/commit.
if grep -RIEq '(BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|ghp_[A-Za-z0-9]+|github_pat_|sk-[A-Za-z0-9]|password[[:space:]]*[:=]|token[[:space:]]*[:=])' "$SCORES_DIR" "$DASH_DIR" 2>/dev/null; then
  echo "AEGIS export aborted: possible secret material detected" >&2
  exit 2
fi

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1"
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1"
  else
    echo "AEGIS export aborted: no SHA-256 utility available" >&2
    exit 4
  fi
}

(
  cd "$SCORES_DIR"
  : > manifest.sha256
  for f in latest.json deepdiag.json history.jsonl; do
    [[ -f "$f" ]] && hash_file "$f" >> manifest.sha256
  done
)

echo "AEGIS export complete: $SCORES_DIR and $DASH_DIR"
