#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${AEGIS_REPO_URL:-https://github.com/thepointer1982-maker/Ugreen-server.git}"
BRANCH="${AEGIS_BRANCH:-aegis/guardian-learning-mcp}"
DEST="${AEGIS_DEST:-$HOME/aegis/Ugreen-server}"

for cmd in git python3 systemctl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd missing" >&2; exit 3; }
done

mkdir -p "$(dirname "$DEST")"
chmod 700 "$(dirname "$DEST")"

if [[ ! -d "$DEST/.git" ]]; then
  git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$DEST"
else
  git -C "$DEST" remote set-url origin "$REPO_URL"
  git -C "$DEST" fetch --prune origin "$BRANCH"
  git -C "$DEST" checkout -B "$BRANCH" "origin/$BRANCH"
  git -C "$DEST" reset --hard "origin/$BRANCH"
  git -C "$DEST" clean -fdx
fi

cd "$DEST"
HEAD_SHA="$(git rev-parse HEAD)"
echo "repo=$DEST"
echo "head=$HEAD_SHA"

bash scripts/aegis_pull_control_install.sh

echo "=== AEGIS PULL CONTROL FIRST RUN ==="
set +e
python3 scripts/aegis_pull_control.py --repo-root "$DEST"
rc=$?
set -e

STATE="$HOME/.local/state/aegis-pull-control/state.json"
if [[ -s "$STATE" ]]; then
  echo "=== AEGIS PULL CONTROL STATE ==="
  cat "$STATE"
else
  echo "state file missing: $STATE" >&2
  exit 5
fi

if [[ "$rc" -ne 0 ]]; then
  echo "pull-control first run returned rc=$rc" >&2
  exit "$rc"
fi

echo "AEGIS zero-cost pull control active."
