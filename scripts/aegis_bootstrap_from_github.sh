#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${AEGIS_BOOTSTRAP_REPO_URL:-https://github.com/thepointer1982-maker/Ugreen-server.git}"
BRANCH="${AEGIS_BOOTSTRAP_BRANCH:-aegis/guardian-learning-mcp}"
DEST="${AEGIS_BOOTSTRAP_DEST:-$HOME/aegis-bootstrap/Ugreen-server}"

for cmd in git bash python3 curl; do
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
echo "bootstrap_repo=$DEST"
echo "bootstrap_head=$(git rev-parse HEAD)"

bash scripts/aegis_access_bootstrap.sh preflight
bash scripts/aegis_access_bootstrap.sh runner

echo "AEGIS access bootstrap completed."
echo "The queued aegis-control status job should now be eligible for aegis-ugreen-v2."
