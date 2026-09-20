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

usage() {
  cat <<'EOF'
Usage: bash scripts/aegis_runner_install.sh

Token discovery order:
1. AEGIS_RUNNER_TOKEN from the current shell
2. existing authenticated GitHub CLI session via gh api

Security:
- short-lived registration token is held only in process memory
- token is never printed or written to the repository
- persistent GitHub CLI credentials are never read by this script
- runner receives versioned label aegis-ugreen-v2
- old queued aegis-ugreen jobs cannot match this runner
- runtime code is checked out into ~/aegis-runtime/Ugreen-server
- control request must include a tested trusted_sha
EOF
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

TOKEN_SOURCE="environment"
if [[ -z "$RUNNER_TOKEN" ]]; then
  TOKEN_SOURCE="none"
  if command -v gh >/dev/null 2>&1 && gh auth status --hostname github.com >/dev/null 2>&1; then
    set +e
    RUNNER_TOKEN="$(gh api \
      --method POST \
      -H "Accept: application/vnd.github+json" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      "repos/$RUNNER_REPO/actions/runners/registration-token" \
      --jq '.token' 2>/dev/null)
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
  echo "No runner registration token available." >&2
  echo "Export AEGIS_RUNNER_TOKEN, or authenticate gh with repository Administration write permission." >&2
  exit 4
fi

trap 'RUNNER_TOKEN=""; unset RUNNER_TOKEN' EXIT
echo "runner_token_source=$TOKEN_SOURCE"

arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) pkg_arch="x64" ;;
  aarch64|arm64) pkg_arch="arm64" ;;
  *) echo "Unsupported architecture: $arch" >&2; exit 5 ;;
esac

for cmd in curl tar git; do
  command -v "$cmd" >/dev/null || { echo "$cmd missing" >&2; exit 3; }
done

mkdir -p "$RUNNER_DIR" "$RUNTIME_ROOT"
chmod 700 "$RUNNER_DIR" "$RUNTIME_ROOT"

runtime_repo="$RUNTIME_ROOT/Ugreen-server"
if [[ ! -d "$runtime_repo/.git" ]]; then
  git clone --no-checkout "$RUNNER_URL.git" "$runtime_repo"
else
  git -C "$runtime_repo" remote set-url origin "$RUNNER_URL.git"
fi

cd "$RUNNER_DIR"
pkg="actions-runner-linux-$pkg_arch-$RUNNER_VERSION.tar.gz"
url="https://github.com/actions/runner/releases/download/v$RUNNER_VERSION/$pkg"

if [[ ! -f "$pkg" ]]; then
  curl -fL --retry 3 --retry-delay 2 -o "$pkg" "$url"
fi

if [[ ! -x ./config.sh ]]; then
  tar xzf "$pkg"
fi

./config.sh \
  --unattended \
  --replace \
  --url "$RUNNER_URL" \
  --token "$RUNNER_TOKEN" \
  --name "$RUNNER_NAME" \
  --labels "$RUNNER_LABELS" \
  --work "_work"

RUNNER_TOKEN=""
unset RUNNER_TOKEN

if [[ -x ./svc.sh ]] && command -v sudo >/dev/null; then
  sudo ./svc.sh install "$(id -un)"
  sudo ./svc.sh start
  sudo ./svc.sh status
else
  echo "Runner configured. Start manually with: $RUNNER_DIR/run.sh"
fi
