#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${AEGIS_REPO_URL:-https://github.com/thepointer1982-maker/Ugreen-server.git}"
BRANCH="${AEGIS_BRANCH:-aegis/resume-pre-lenovo-20260920}"
TRUSTED_SHA="${AEGIS_TRUSTED_SHA:-}"
DEST="${AEGIS_DEST:-$HOME/aegis/Ugreen-server}"

if [[ -z "$TRUSTED_SHA" || ! "$TRUSTED_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "AEGIS_TRUSTED_SHA must be a full 40-character lowercase commit SHA." >&2
  exit 2
fi

for cmd in git python3 systemctl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd missing" >&2; exit 3; }
done

mkdir -p "$(dirname "$DEST")"
chmod 700 "$(dirname "$DEST")"

if [[ ! -d "$DEST/.git" ]]; then
  git clone --no-checkout "$REPO_URL" "$DEST"
else
  git -C "$DEST" remote set-url origin "$REPO_URL"
fi

git -C "$DEST" fetch --prune origin "$BRANCH"
git -C "$DEST" cat-file -e "$TRUSTED_SHA^{commit}"
if ! git -C "$DEST" merge-base --is-ancestor "$TRUSTED_SHA" "origin/$BRANCH"; then
  echo "blocked: trusted SHA is not an ancestor of origin/$BRANCH" >&2
  exit 4
fi
git -C "$DEST" checkout --detach --force "$TRUSTED_SHA"
git -C "$DEST" reset --hard "$TRUSTED_SHA"
git -C "$DEST" clean -fdx

cd "$DEST"
HEAD_SHA="$(git rev-parse HEAD)"
[[ "$HEAD_SHA" == "$TRUSTED_SHA" ]] || {
  echo "blocked: checked out head does not match trusted SHA" >&2
  exit 4
}
echo "repo=$DEST"
echo "head=$HEAD_SHA"

echo "=== AEGIS PRIMARY ZERO-COST CONTROL ==="
bash scripts/aegis_pull_control_install.sh

echo "=== AEGIS OPTIONAL RETURN CHANNEL ==="
RUNNER_STATUS="skipped"
if command -v gh >/dev/null 2>&1 && gh auth status --hostname github.com >/dev/null 2>&1; then
  echo "github_cli_auth=ready"
  set +e
  bash scripts/aegis_access_bootstrap.sh runner
  runner_rc=$?
  set -e
  if [[ "$runner_rc" -eq 0 ]]; then
    RUNNER_STATUS="active"
  else
    RUNNER_STATUS="failed:$runner_rc"
  fi
else
  echo "github_cli_auth=unavailable"
fi
echo "runner_return_channel=$RUNNER_STATUS"

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
echo "runner_return_channel=$RUNNER_STATUS"
