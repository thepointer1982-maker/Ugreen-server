#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_ROOT="${AEGIS_SOURCE_ROOT:-$HOME/Library/Application Support/AEGIS-Device-AI}"
REPO_ROOT="${1:-$(pwd)}"
SCORES_DIR="$REPO_ROOT/scores"
DASH_DIR="$REPO_ROOT/dashboard"
TMP_DIR="${TMPDIR:-/tmp}/aegis-export.$$"
trap 'rm -rf "$TMP_DIR"' EXIT
mkdir -p "$TMP_DIR" "$SCORES_DIR" "$DASH_DIR"

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

(
  cd "$SCORES_DIR"
  : > manifest.sha256
  for f in latest.json deepdiag.json history.jsonl; do
    [[ -f "$f" ]] && shasum -a 256 "$f" >> manifest.sha256
  done
)

echo "AEGIS export complete: $SCORES_DIR and $DASH_DIR"
