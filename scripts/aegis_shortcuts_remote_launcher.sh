#!/usr/bin/env bash
set -euo pipefail

DESCRIPTOR_URL="${AEGIS_CONTROL_DESCRIPTOR_URL:-https://raw.githubusercontent.com/thepointer1982-maker/Ugreen-server/aegis-control/.aegis-control/bootstrap.json}"
TMP_DIR="${TMPDIR:-/tmp}"
TMP_JSON="$(mktemp "$TMP_DIR/aegis-shortcuts-json.XXXXXX")"
TMP_SCRIPT="$(mktemp "$TMP_DIR/aegis-shortcuts-bootstrap.XXXXXX")"
cleanup() { rm -f "$TMP_JSON" "$TMP_SCRIPT"; }
trap cleanup EXIT HUP INT TERM

for cmd in curl python3 bash; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd missing" >&2; exit 3; }
done

curl -fsSL --connect-timeout 10 --max-time 30 "$DESCRIPTOR_URL" -o "$TMP_JSON"

VALIDATED="$(python3 - "$TMP_JSON" <<'PY'
import json, re, sys
from pathlib import Path
from urllib.parse import urlparse

p = Path(sys.argv[1])
d = json.loads(p.read_text(encoding="utf-8"))
if d.get("schema") != "aegis-iphone-bootstrap/v1":
    raise SystemExit("blocked: unexpected descriptor schema")
sha = d.get("trusted_sha")
if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
    raise SystemExit("blocked: invalid trusted_sha")
sequence = d.get("control_sequence")
if not isinstance(sequence, int) or sequence < 1:
    raise SystemExit("blocked: invalid control_sequence")
url = d.get("bootstrap_url")
if not isinstance(url, str):
    raise SystemExit("blocked: bootstrap_url missing")
u = urlparse(url)
if u.scheme != "https" or u.netloc != "raw.githubusercontent.com":
    raise SystemExit("blocked: bootstrap_url origin not allowed")
expected = f"/thepointer1982-maker/Ugreen-server/{sha}/scripts/aegis_zero_cost_bootstrap.sh"
if u.path != expected or u.query or u.fragment:
    raise SystemExit("blocked: bootstrap_url does not match trusted_sha")
print(sha)
print(sequence)
print(url)
PY
)"

SHA="$(printf "%s\n" "$VALIDATED" | sed -n '1p')"
SEQUENCE="$(printf "%s\n" "$VALIDATED" | sed -n '2p')"
BOOTSTRAP_URL="$(printf "%s\n" "$VALIDATED" | sed -n '3p')"

echo "AEGIS Shortcuts launcher trusted_sha=$SHA sequence=$SEQUENCE"
curl -fsSL --connect-timeout 10 --max-time 30 "$BOOTSTRAP_URL" -o "$TMP_SCRIPT"

AEGIS_TRUSTED_SHA="$SHA" bash "$TMP_SCRIPT"

STATE="$HOME/.local/state/aegis-pull-control/state.json"
echo "=== AEGIS SHORTCUTS RESULT ==="
if [[ -s "$STATE" ]]; then
  cat "$STATE"
else
  echo '{"status":"unknown","error":"pull-control-state-missing"}'
  exit 5
fi
