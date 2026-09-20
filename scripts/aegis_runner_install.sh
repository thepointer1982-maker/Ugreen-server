#!/usr/bin/env bash
set -euo pipefail

RUNNER_VERSION="${AEGIS_RUNNER_VERSION:-2.337.0}"
RUNNER_URL="${AEGIS_RUNNER_URL:-https://github.com/thepointer1982-maker/Ugreen-server}"
RUNNER_REPO="${AEGIS_RUNNER_REPO:-thepointer1982-maker/Ugreen-server}"
RUNNER_TOKEN="${AEGIS_RUNNER_TOKEN:-}"
RUNNER_NAME="${AEGIS_RUNNER_NAME:-aegis-ugreen-v2}"
RUNNER_LABELS="${AEGIS_RUNNER_LABELS:-aegis-ugreen-v2}"
RUNNER_DIR="${AEGIS_RUNNER_DIR:-$HOME/actions-runner-aegis-v2}"
RUNTIME_ROOT="${AEGIS_RUNTIME_ROOT:-$HOME/aegis-runtime}"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/aegis-github-runner.service"
STATE_DIR="$HOME/.local/state/aegis-runner"
STATE_FILE="$STATE_DIR/status.json"

usage() {
  cat <<'EOF'
Usage: bash scripts/aegis_runner_install.sh

Behavior:
- reuses an already configured runner without requesting a new token
- otherwise discovers a short-lived registration token from AEGIS_RUNNER_TOKEN or gh api
- installs a user-systemd service when available, avoiding interactive sudo
- falls back to GitHub's svc.sh only when noninteractive sudo is already available

Security:
- short-lived registration token is held only in process memory
- token is never printed or written to the repository
- no router port is opened
- runtime code is checked out into ~/aegis-runtime/Ugreen-server
- control request must include a tested trusted_sha
EOF
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

for cmd in curl tar git python3; do
  command -v "$cmd" >/dev/null || { echo "$cmd missing" >&2; exit 3; }
done

mkdir -p "$RUNNER_DIR" "$RUNTIME_ROOT" "$UNIT_DIR" "$STATE_DIR"
chmod 700 "$RUNNER_DIR" "$RUNTIME_ROOT" "$STATE_DIR"

runtime_repo="$RUNTIME_ROOT/Ugreen-server"
if [[ ! -d "$runtime_repo/.git" ]]; then
  git clone --no-checkout "$RUNNER_URL.git" "$runtime_repo"
else
  git -C "$runtime_repo" remote set-url origin "$RUNNER_URL.git"
fi

arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) pkg_arch="x64" ;;
  aarch64|arm64) pkg_arch="arm64" ;;
  *) echo "Unsupported architecture: $arch" >&2; exit 5 ;;
esac

cd "$RUNNER_DIR"
configured=0
[[ -s .runner && -x ./run.sh ]] && configured=1

if [[ "$configured" -ne 1 ]]; then
  pkg="actions-runner-linux-$pkg_arch-$RUNNER_VERSION.tar.gz"
  url="https://github.com/actions/runner/releases/download/v$RUNNER_VERSION/$pkg"

  if [[ ! -f "$pkg" ]]; then
    curl -fL --retry 3 --retry-delay 2 -o "$pkg" "$url"
  fi
  if [[ ! -x ./config.sh ]]; then
    tar xzf "$pkg"
  fi

  TOKEN_SOURCE="environment"
  if [[ -z "$RUNNER_TOKEN" ]]; then
    TOKEN_SOURCE="none"
    if command -v gh >/dev/null 2>&1 && gh auth status --hostname github.com >/dev/null 2>&1; then
      set +e
      RUNNER_TOKEN="$(gh api         --method POST         -H "Accept: application/vnd.github+json"         -H "X-GitHub-Api-Version: 2022-11-28"         "repos/$RUNNER_REPO/actions/runners/registration-token"         --jq '.token' 2>/dev/null)"
      gh_rc=$?
      set -e
      if [[ "$gh_rc" -eq 0 && -n "$RUNNER_TOKEN" ]]; then
        TOKEN_SOURCE="gh-api"
      else
        RUNNER_TOKEN=""
      fi
    fi
  fi

  if [[ -z "$RUNNER_TOKEN" ]]; then
    echo "No runner registration token available for first-time configuration." >&2
    exit 4
  fi

  trap 'RUNNER_TOKEN=""; unset RUNNER_TOKEN' EXIT
  echo "runner_token_source=$TOKEN_SOURCE"
  ./config.sh     --unattended     --replace     --url "$RUNNER_URL"     --token "$RUNNER_TOKEN"     --name "$RUNNER_NAME"     --labels "$RUNNER_LABELS"     --work "_work"
  RUNNER_TOKEN=""
  unset RUNNER_TOKEN
  trap - EXIT
  configured=1
else
  echo "runner_config=reused"
fi

service_mode="manual"
service_active=0

if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  cat > "$UNIT" <<EOF
[Unit]
Description=AEGIS GitHub Actions runner
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$RUNNER_DIR
ExecStart=$RUNNER_DIR/run.sh
Restart=always
RestartSec=3
TimeoutStopSec=45
KillSignal=SIGINT
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$RUNNER_DIR $RUNTIME_ROOT
UMask=0077

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now aegis-github-runner.service
  if systemctl --user is-active --quiet aegis-github-runner.service; then
    service_mode="user-systemd"
    service_active=1
  fi
elif [[ -x ./svc.sh ]] && command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
  sudo -n ./svc.sh install "$(id -un)" >/dev/null 2>&1 || true
  sudo -n ./svc.sh start >/dev/null 2>&1 || true
  service_mode="system-svc"
  if sudo -n ./svc.sh status 2>/dev/null | grep -qi "active"; then
    service_active=1
  fi
else
  echo "Runner configured but no persistent service manager is available." >&2
fi

export AEGIS_RUNNER_STATE_FILE="$STATE_FILE"
export AEGIS_RUNNER_CONFIGURED="$configured"
export AEGIS_RUNNER_SERVICE_MODE="$service_mode"
export AEGIS_RUNNER_SERVICE_ACTIVE="$service_active"
export AEGIS_RUNNER_NAME_OUT="$RUNNER_NAME"
export AEGIS_RUNNER_LABELS_OUT="$RUNNER_LABELS"

python3 - <<'PY'
import json, os, tempfile
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["AEGIS_RUNNER_STATE_FILE"])
data = {
    "schema": "aegis-runner/v2",
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "configured": os.environ["AEGIS_RUNNER_CONFIGURED"] == "1",
    "service_mode": os.environ["AEGIS_RUNNER_SERVICE_MODE"],
    "service_active": os.environ["AEGIS_RUNNER_SERVICE_ACTIVE"] == "1",
    "runner_name": os.environ["AEGIS_RUNNER_NAME_OUT"],
    "labels": os.environ["AEGIS_RUNNER_LABELS_OUT"].split(","),
    "security": {
        "registration_token_persisted": False,
        "interactive_sudo_required": False,
        "opened_router_ports": False,
    },
}
path.parent.mkdir(parents=True, exist_ok=True)
fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
finally:
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
print(json.dumps(data, ensure_ascii=False))
PY

if [[ "$service_active" -ne 1 ]]; then
  echo "AEGIS_RUNNER status=degraded configured=$configured service_mode=$service_mode" >&2
  exit 6
fi

echo "AEGIS_RUNNER status=healthy mode=$service_mode"
