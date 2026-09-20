#!/usr/bin/env sh
set -eu

DESCRIPTOR_URL="${AEGIS_BOOTSTRAP_DESCRIPTOR_URL:-https://raw.githubusercontent.com/thepointer1982-maker/Ugreen-server/aegis-control/.aegis-control/bootstrap.json}"
NAS_USER="${AEGIS_NAS_USER:-${1:-}}"

if [ -z "$NAS_USER" ]; then
  echo "Usage: AEGIS_NAS_USER=<nas-user> sh aegis_iphone_ashell_bootstrap.sh" >&2
  echo "or:    sh aegis_iphone_ashell_bootstrap.sh <nas-user>" >&2
  exit 2
fi

for cmd in curl python3 ssh mktemp; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd missing in iPhone shell" >&2; exit 3; }
done

TMP_JSON="$(mktemp -t aegis-bootstrap-json.XXXXXX)"
TMP_SCRIPT="$(mktemp -t aegis-bootstrap-script.XXXXXX)"
cleanup() { rm -f "$TMP_JSON" "$TMP_SCRIPT"; }
trap cleanup EXIT HUP INT TERM

curl -fsSL --connect-timeout 10 --max-time 30 "$DESCRIPTOR_URL" -o "$TMP_JSON"

VALIDATED="$(python3 - "$TMP_JSON" <<'PY'
import ipaddress, json, re, sys
from pathlib import Path
p = Path(sys.argv[1])
d = json.loads(p.read_text(encoding="utf-8"))
if d.get("schema") != "aegis-iphone-bootstrap/v1":
    raise SystemExit("blocked: unexpected bootstrap schema")
sha = d.get("trusted_sha")
if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
    raise SystemExit("blocked: invalid trusted_sha")
host = d.get("nas_lan_host")
try:
    ip = ipaddress.ip_address(host)
except Exception:
    raise SystemExit("blocked: NAS host is not an IP address")
if not ip.is_private:
    raise SystemExit("blocked: NAS host is not private/LAN")
port = d.get("ssh_port")
if not isinstance(port, int) or not (1 <= port <= 65535):
    raise SystemExit("blocked: invalid SSH port")
url = d.get("bootstrap_url")
expected = f"https://raw.githubusercontent.com/thepointer1982-maker/Ugreen-server/{sha}/scripts/aegis_zero_cost_bootstrap.sh"
if url != expected:
    raise SystemExit("blocked: bootstrap URL does not match trusted SHA")
print(host)
print(port)
print(sha)
print(url)
PY
)"

HOST="$(printf "%s\n" "$VALIDATED" | sed -n '1p')"
PORT="$(printf "%s\n" "$VALIDATED" | sed -n '2p')"
SHA="$(printf "%s\n" "$VALIDATED" | sed -n '3p')"
BOOTSTRAP_URL="$(printf "%s\n" "$VALIDATED" | sed -n '4p')"

echo "AEGIS iPhone bootstrap target=$HOST port=$PORT sha=$SHA"
curl -fsSL --connect-timeout 10 --max-time 30 "$BOOTSTRAP_URL" -o "$TMP_SCRIPT"

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh" 2>/dev/null || true

SSH_OPTS="-p $PORT -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=ask"

echo "Connecting to UGREEN over local SSH. First connection may ask to confirm the host fingerprint."
# shellcheck disable=SC2086
ssh $SSH_OPTS "$NAS_USER@$HOST" "AEGIS_TRUSTED_SHA=$SHA bash -s" < "$TMP_SCRIPT"

echo "=== AEGIS REMOTE STATE ==="
# shellcheck disable=SC2086
ssh $SSH_OPTS "$NAS_USER@$HOST" '
  set -e
  printf "pull_control_timer="
  systemctl --user is-active aegis-pull-control.timer 2>/dev/null || true
  STATE="$HOME/.local/state/aegis-pull-control/state.json"
  if [ -s "$STATE" ]; then
    cat "$STATE"
  else
    echo "state-missing"
  fi
'

echo "AEGIS iPhone -> UGREEN bootstrap finished."
