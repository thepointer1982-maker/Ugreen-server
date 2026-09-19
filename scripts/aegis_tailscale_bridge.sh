#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_DIR="${AEGIS_TAILSCALE_DIR:-$HOME/.local/share/aegis-tailscale}"
COMPOSE_SRC="$ROOT/deploy/tailscale/docker-compose.yml"
SSH_TAILNET_PORT="${AEGIS_TAILSCALE_SSH_PORT:-2222}"

usage() {
  cat <<'EOF'
Usage: aegis_tailscale_bridge.sh start|stop|status|serve-ssh|reset-serve

This is an opt-in maintenance bridge:
- no router port forwarding
- no public Funnel
- auth key is never stored in the repository
- traditional NAS sshd remains the backend
- tailnet TCP port 2222 forwards to localhost:22

For first authentication export TS_AUTHKEY only in the local shell, or login
to Tailscale interactively before enabling serve-ssh.
EOF
}

compose() {
  (cd "$STACK_DIR" && docker compose "$@")
}

require_docker() {
  command -v docker >/dev/null || { echo "docker missing" >&2; exit 3; }
  docker compose version >/dev/null 2>&1 || { echo "docker compose missing" >&2; exit 3; }
  [[ -c /dev/net/tun ]] || { echo "/dev/net/tun missing" >&2; exit 3; }
}

prepare() {
  mkdir -p "$STACK_DIR/state"
  chmod 700 "$STACK_DIR" "$STACK_DIR/state"
  cp "$COMPOSE_SRC" "$STACK_DIR/docker-compose.yml"
}

ts() {
  docker exec aegis-tailscale tailscale "$@"
}

cmd="${1:-}"
case "$cmd" in
  start)
    require_docker
    prepare
    compose up -d
    if [[ -n "${TS_AUTHKEY:-}" ]]; then
      ts up --auth-key="$TS_AUTHKEY" --accept-dns=false --accept-routes=false
    fi
    ts status || true
    ;;
  serve-ssh)
    require_docker
    prepare
    compose up -d
    if ! ts status >/dev/null 2>&1; then
      echo "Tailscale not authenticated. Export TS_AUTHKEY and run start first." >&2
      exit 4
    fi
    ts serve --bg --tcp="$SSH_TAILNET_PORT" tcp://127.0.0.1:22
    ts serve status
    ;;
  reset-serve)
    require_docker
    ts serve reset
    ;;
  status)
    require_docker
    prepare
    compose ps
    ts status || true
    ts serve status || true
    ;;
  stop)
    [[ -f "$STACK_DIR/docker-compose.yml" ]] || exit 0
    compose down
    ;;
  *)
    usage
    exit 2
    ;;
esac
