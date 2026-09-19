#!/usr/bin/env bash
set -euo pipefail

RUNNER_VERSION="${AEGIS_RUNNER_VERSION:-2.337.0}"
RUNNER_URL="${AEGIS_RUNNER_URL:-https://github.com/thepointer1982-maker/Ugreen-server}"
RUNNER_TOKEN="${AEGIS_RUNNER_TOKEN:-}"
RUNNER_NAME="${AEGIS_RUNNER_NAME:-aegis-ugreen}"
RUNNER_LABELS="${AEGIS_RUNNER_LABELS:-aegis-ugreen}"
RUNNER_DIR="${AEGIS_RUNNER_DIR:-$HOME/actions-runner}"

usage() {
  cat <<'EOF'
Usage: AEGIS_RUNNER_TOKEN=... bash scripts/aegis_runner_install.sh

Installs a repository-scoped GitHub Actions self-hosted runner for the UGREEN NAS.

Security:
- registration token is read only from AEGIS_RUNNER_TOKEN
- token is never written to the repository
- runner receives custom label aegis-ugreen
- control workflow is owner-only and allowlisted
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

command -v curl >/dev/null || { echo "curl missing" >&2; exit 3; }
command -v tar >/dev/null || { echo "tar missing" >&2; exit 3; }

mkdir -p "$RUNNER_DIR"
chmod 700 "$RUNNER_DIR"
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
