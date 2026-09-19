#!/usr/bin/env bash
set -euo pipefail

RUNNER_VERSION="${AEGIS_RUNNER_VERSION:-2.337.0}"
RUNNER_URL="${AEGIS_RUNNER_URL:-https://github.com/thepointer1982-maker/Ugreen-server}"
RUNNER_TOKEN="${AEGIS_RUNNER_TOKEN:-}"
RUNNER_NAME="${AEGIS_RUNNER_NAME:-aegis-ugreen-v2}"
RUNNER_LABELS="${AEGIS_RUNNER_LABELS:-aegis-ugreen-v2}"
RUNNER_DIR="${AEGIS_RUNNER_DIR:-$HOME/actions-runner-aegis-v2}"
RUNTIME_ROOT="${AEGIS_RUNTIME_ROOT:-$HOME/aegis-runtime}"

usage() {
  cat <<'EOF'
Usage: AEGIS_RUNNER_TOKEN=... bash scripts/aegis_runner_install.sh

Installs the repository-scoped AEGIS UGREEN self-hosted runner.

Security:
- registration token is read only from AEGIS_RUNNER_TOKEN
- token is never written to the repository
- runner receives versioned label aegis-ugreen-v2
- old queued aegis-ugreen jobs cannot match this runner
- runtime code is checked out into ~/aegis-runtime/Ugreen-server
- control workflow accepts only owner pushes and an allowlisted action
- control request must include a tested trusted_sha
- do not register this runner for public PR workflows
EOF
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

[[ -n "$RUNNER_TOKEN" ]] || {
  echo "AEGIS_RUNNER_TOKEN missing" >&2
  exit 4
}

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

if [[ -x ./svc.sh ]] && command -v sudo >/dev/null; then
  sudo ./svc.sh install "$(id -un)"
  sudo ./svc.sh start
  sudo ./svc.sh status
else
  echo "Runner configured. Start manually with: $RUNNER_DIR/run.sh"
fi
