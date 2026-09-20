#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-preflight}"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/aegis_access_bootstrap.sh preflight
  bash scripts/aegis_access_bootstrap.sh runner
  bash scripts/aegis_access_bootstrap.sh tailscale-login
  bash scripts/aegis_access_bootstrap.sh tailscale-ssh
  bash scripts/aegis_access_bootstrap.sh all

Runner credentials:
- AEGIS_RUNNER_TOKEN if explicitly provided
- otherwise an existing authenticated GitHub CLI (gh) session is used to
  request a short-lived runner registration token automatically

Environment:
  TS_AUTHKEY optional Tailscale auth key; otherwise interactive login URL

Purpose:
- establish an outbound GitHub control channel to the NAS
- optionally establish a private Tailnet SSH path
- never open router ports
- never persist runner registration tokens in the repository
EOF
}

gh_runner_capable() {
  command -v gh >/dev/null 2>&1 || return 1
  gh auth status --hostname github.com >/dev/null 2>&1 || return 1
  return 0
}

preflight() {
  echo "=== AEGIS ACCESS PREFLIGHT ==="
  echo "host=$(hostname 2>/dev/null || true)"
  echo "user=$(id -un)"
  echo "uid=$(id -u)"
  echo "kernel=$(uname -srmo 2>/dev/null || true)"
  echo "arch=$(uname -m)"

  for cmd in git python3 curl tar gh; do
    if command -v "$cmd" >/dev/null 2>&1; then
      echo "$cmd=ok path=$(command -v "$cmd")"
    else
      echo "$cmd=missing"
    fi
  done

  if gh_runner_capable; then
    echo "github_cli_auth=ready"
  else
    echo "github_cli_auth=unavailable"
  fi

  if command -v docker >/dev/null 2>&1; then
    echo "docker=ok"
    docker version --format 'docker_server={{.Server.Version}}' 2>/dev/null || true
    docker compose version 2>/dev/null || true
  else
    echo "docker=missing"
  fi

  if [[ -c /dev/net/tun ]]; then
    echo "tun=ok"
  else
    echo "tun=missing"
  fi

  if command -v ss >/dev/null 2>&1; then
    if ss -ltn 2>/dev/null | grep -Eq '(^|[[:space:]])[^[:space:]]*:22[[:space:]]'; then
      echo "ssh_port_22=listening"
    else
      echo "ssh_port_22=not-detected"
    fi
  fi

  if command -v systemctl >/dev/null 2>&1; then
    systemctl is-active ssh 2>/dev/null && echo "ssh_service=active" || true
    systemctl is-active sshd 2>/dev/null && echo "sshd_service=active" || true
  fi

  if curl -IsS --max-time 5 https://github.com/ >/dev/null 2>&1; then
    echo "github_https=reachable"
  else
    echo "github_https=unavailable"
  fi
}

runner() {
  bash "$ROOT/scripts/aegis_runner_install.sh"
}

tailscale_login() {
  bash "$ROOT/scripts/aegis_tailscale_bridge.sh" start
  if [[ -z "${TS_AUTHKEY:-}" ]]; then
    echo "No TS_AUTHKEY provided; requesting interactive Tailscale login URL..."
    docker exec aegis-tailscale tailscale up --accept-dns=false --accept-routes=false || true
  fi
}

tailscale_ssh() {
  bash "$ROOT/scripts/aegis_tailscale_bridge.sh" serve-ssh
  ipv4="$(docker exec aegis-tailscale tailscale ip -4 2>/dev/null | head -n1 || true)"
  if [[ -n "$ipv4" ]]; then
    echo "tailnet_ipv4=$ipv4"
    echo "ssh_example=ssh -p ${AEGIS_TAILSCALE_SSH_PORT:-2222} <admin-user>@$ipv4"
  fi
}

case "$MODE" in
  preflight)
    preflight
    ;;
  runner)
    preflight
    runner
    ;;
  tailscale-login)
    preflight
    tailscale_login
    ;;
  tailscale-ssh)
    preflight
    tailscale_ssh
    ;;
  all)
    preflight
    if [[ -n "${AEGIS_RUNNER_TOKEN:-}" ]] || gh_runner_capable; then
      runner
    else
      echo "runner=skipped reason=no-explicit-token-and-no-authorized-gh-session"
    fi
    if command -v docker >/dev/null 2>&1 && [[ -c /dev/net/tun ]]; then
      tailscale_login
    else
      echo "tailscale=skipped reason=docker-or-tun-missing"
    fi
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
