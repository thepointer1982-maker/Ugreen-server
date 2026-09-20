#!/usr/bin/env bash
set -euo pipefail

ROOT="${AEGIS_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ALLOW="${AEGIS_ALLOW_OPENCODE_INSTALL:-0}"
VERSION="${AEGIS_OPENCODE_VERSION:-1.18.31}"
BIN_DIR="${AEGIS_LOCAL_BIN_DIR:-$HOME/.local/bin}"
TARGET="$BIN_DIR/opencode"

[[ "$ALLOW" == "0" || "$ALLOW" == "1" ]] || {
  echo "AEGIS_ALLOW_OPENCODE_INSTALL must be 0 or 1" >&2
  exit 64
}
[[ "$VERSION" == "1.18.31" ]] || {
  echo "blocked: unreviewed OpenCode version $VERSION" >&2
  exit 4
}

mkdir -p "$BIN_DIR"
chmod 700 "$BIN_DIR" 2>/dev/null || true

if command -v opencode >/dev/null 2>&1; then
  set +e
  python3 "$ROOT/scripts/aegis_opencode_guard.py" --require-modern
  rc=$?
  set -e
  if [[ "$rc" -eq 0 ]]; then
    echo "AEGIS_OPENCODE status=reused-modern"
    exit 0
  fi
fi

if [[ "$ALLOW" != "1" ]]; then
  python3 "$ROOT/scripts/aegis_opencode_guard.py" || true
  echo "AEGIS_OPENCODE status=optional-install-disabled"
  exit 0
fi

for cmd in curl tar sha256sum python3; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "$cmd missing" >&2
    exit 3
  }
done

arch="$(uname -m)"
case "$arch" in
  x86_64|amd64)
    asset="opencode-linux-x64.tar.gz"
    expected="e9312be75ed803b7415fc2aeabda1f4fe938912a39673762dc0c38c0e11ebde4"
    ;;
  aarch64|arm64)
    asset="opencode-linux-arm64.tar.gz"
    expected="d4e332f46b227448582c0d9fc75f6f826dfe95c9f751bc2011fc4d937a042be6"
    ;;
  *)
    echo "unsupported architecture: $arch" >&2
    exit 5
    ;;
esac

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
url="https://github.com/anomalyco/opencode/releases/download/v$VERSION/$asset"

curl -fL --retry 3 --retry-delay 2 -o "$tmp/$asset" "$url"
printf '%s  %s\n' "$expected" "$tmp/$asset" | sha256sum -c -
tar -xzf "$tmp/$asset" -C "$tmp"

candidate="$(find "$tmp" -maxdepth 2 -type f -name opencode -print -quit)"
[[ -n "$candidate" ]] || {
  echo "blocked: OpenCode binary missing from verified archive" >&2
  exit 6
}
install -m 700 "$candidate" "$TARGET"

PATH="$BIN_DIR:$PATH" python3 "$ROOT/scripts/aegis_opencode_guard.py" --binary "$TARGET" --require-modern
echo "AEGIS_OPENCODE status=installed version=$VERSION target=$TARGET"
