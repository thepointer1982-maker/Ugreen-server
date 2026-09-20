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

echo "=== AEGIS USER SERVICE PERSISTENCE ==="
PERSISTENCE_STATUS="unknown"
if [[ -f scripts/aegis_user_persistence.sh ]]; then
  set +e
  bash scripts/aegis_user_persistence.sh
  persistence_rc=$?
  set -e
  if [[ "$persistence_rc" -eq 0 ]]; then
    PERSISTENCE_STATUS="checked"
  else
    PERSISTENCE_STATUS="failed:$persistence_rc"
  fi
fi
echo "user_persistence=$PERSISTENCE_STATUS"

echo "=== AEGIS PRIMARY ZERO-COST CONTROL ==="
bash scripts/aegis_pull_control_install.sh

echo "=== AEGIS LOCAL MCP RUNTIME ==="
MCP_STATUS="skipped"
set +e
AEGIS_ALLOW_MCP_INSTALL="${AEGIS_ALLOW_MCP_INSTALL:-1}" bash scripts/aegis_mcp_runtime_install.sh
mcp_rc=$?
set -e
if [[ "$mcp_rc" -eq 0 ]]; then
  MCP_STATUS="ready"
else
  MCP_STATUS="failed:$mcp_rc"
fi
echo "mcp_runtime=$MCP_STATUS"
echo "docker_efficiency=$DOCKER_EFFICIENCY_STATUS"

echo "=== AEGIS LOCAL CODEX OSS ==="
CODEX_OSS_STATUS="skipped"
set +e
AEGIS_ALLOW_CODEX_INSTALL="${AEGIS_ALLOW_CODEX_INSTALL:-1}" \
AEGIS_ALLOW_MODEL_DOWNLOAD="${AEGIS_ALLOW_MODEL_DOWNLOAD:-1}" \
bash scripts/aegis_codex_oss_install.sh
codex_oss_rc=$?
set -e
if [[ "$codex_oss_rc" -eq 0 ]]; then
  CODEX_OSS_STATUS="ready"
else
  CODEX_OSS_STATUS="failed:$codex_oss_rc"
fi
echo "codex_oss=$CODEX_OSS_STATUS"

echo "=== AEGIS CODER BOOT GUARDIAN ==="
CODER_BOOT_STATUS="skipped"
if systemctl --user show-environment >/dev/null 2>&1; then
  set +e
  AEGIS_CODER_PREFER="${AEGIS_CODER_PREFER:-codex-local}" AEGIS_ALLOW_CLOUD_CODEX="${AEGIS_ALLOW_CLOUD_CODEX:-0}" bash scripts/aegis_coder_boot_install.sh
  coder_boot_rc=$?
  set -e
  if [[ "$coder_boot_rc" -eq 0 ]]; then
    CODER_BOOT_STATUS="active"
  else
    CODER_BOOT_STATUS="failed:$coder_boot_rc"
  fi
else
  CODER_BOOT_STATUS="user-systemd-unavailable"
fi
echo "coder_boot_guardian=$CODER_BOOT_STATUS"

echo "=== AEGIS DOCKER EFFICIENCY ==="
DOCKER_EFFICIENCY_STATUS="skipped"
if command -v docker >/dev/null 2>&1; then
  set +e
  bash scripts/aegis_docker_efficiency_install.sh
  docker_efficiency_rc=$?
  set -e
  if [[ "$docker_efficiency_rc" -eq 0 ]]; then
    DOCKER_EFFICIENCY_STATUS="active"
  else
    DOCKER_EFFICIENCY_STATUS="failed:$docker_efficiency_rc"
  fi
fi
echo "docker_efficiency=$DOCKER_EFFICIENCY_STATUS"

echo "=== AEGIS OPTIONAL RETURN CHANNEL ==="
RUNNER_STATUS="skipped"
set +e
bash scripts/aegis_runner_install.sh
runner_rc=$?
set -e
if [[ "$runner_rc" -eq 0 ]]; then
  RUNNER_STATUS="active"
else
  RUNNER_STATUS="failed:$runner_rc"
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
echo "user_persistence=$PERSISTENCE_STATUS"
echo "mcp_runtime=$MCP_STATUS"
echo "codex_oss=$CODEX_OSS_STATUS"
echo "coder_boot_guardian=$CODER_BOOT_STATUS"
echo "runner_return_channel=$RUNNER_STATUS"
