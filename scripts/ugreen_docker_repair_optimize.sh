#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME="ugreen_docker_repair_optimize"
LOG_DIR="/var/log/ugreen-docker-toolkit"
APPLY=0
PRUNE_REQUESTED=0
RESTART_UNHEALTHY=1
INSTALL_DOCKER=0
CONFIGURE_DAEMON=1
FORCE_DAEMON_CONFIG=0
PROTECTED_DISK_MIN_BYTES=1700000000000
PROTECTED_DISK_MAX_BYTES=1900000000000
PROTECTED_DISK_FOUND=0
PROBE_HOSTS=()
COMPOSE_ROOT="/volume1/docker"
SSD_ROOT=""
DUPLICATE_SCAN_ROOT=""
QUARANTINE_DUPLICATES=0
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_LOG="${LOG_DIR}/${SCRIPT_NAME}_${TIMESTAMP}.log"
DOCKER_AVAILABLE=0

usage() {
  cat <<USAGE
Usage: sudo $0 [--dry-run|--apply] [options]

Safe defaults for repairing and optimizing Docker on a UGREEN NAS/DXP server.

Options:
  --dry-run                Show intended actions without changing the system (default).
  --apply                  Apply repair and optimization actions.
  --install-docker         If Docker is missing, attempt installation through the detected package manager.
  --prune                  Deprecated safety flag. Logs a refusal; no Docker data is deleted.
  --compose-root PATH      Root directory to search for docker-compose.yml files.
                           Default: ${COMPOSE_ROOT}
  --no-restart-unhealthy   Do not restart containers with health status 'unhealthy'.
  --no-daemon-config       Do not create a safe Docker daemon log-rotation config.
  --force-daemon-config    Replace an existing /etc/docker/daemon.json after creating a backup.
  --probe-host HOST         Add a host/IP for connection checks. Can be passed multiple times.
  --ssd-root PATH           SSD path that may be used for new installs, cache, and quarantine.
  --duplicate-scan-root PATH
                           Read-only duplicate scan root. Quarantine requires --ssd-root.
  --quarantine-duplicates   Move duplicate files to SSD quarantine instead of deleting them. Requires --apply.
  -h, --help               Show this help.
USAGE
}

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$RUN_LOG"
}

run_cmd() {
  if [[ "$APPLY" -eq 1 ]]; then
    log "RUN: $*"
    "$@" 2>&1 | tee -a "$RUN_LOG"
  else
    log "DRY-RUN: $*"
  fi
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Please run as root, for example with sudo." >&2
    exit 1
  fi
}

prepare_log_dir() {
  mkdir -p "$LOG_DIR"
  touch "$RUN_LOG"
  chmod 0750 "$LOG_DIR"
  chmod 0640 "$RUN_LOG"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run)
        APPLY=0
        shift
        ;;
      --apply)
        APPLY=1
        shift
        ;;
      --install-docker)
        INSTALL_DOCKER=1
        shift
        ;;
      --prune)
        PRUNE_REQUESTED=1
        shift
        ;;
      --compose-root)
        COMPOSE_ROOT="${2:-}"
        if [[ -z "$COMPOSE_ROOT" ]]; then
          echo "--compose-root requires a path." >&2
          exit 1
        fi
        shift 2
        ;;
      --no-restart-unhealthy)
        RESTART_UNHEALTHY=0
        shift
        ;;
      --no-daemon-config)
        CONFIGURE_DAEMON=0
        shift
        ;;
      --force-daemon-config)
        FORCE_DAEMON_CONFIG=1
        shift
        ;;
      --probe-host)
        if [[ -z "${2:-}" ]]; then
          echo "--probe-host requires a host or IP." >&2
          exit 1
        fi
        PROBE_HOSTS+=("$2")
        shift 2
        ;;
      --ssd-root)
        SSD_ROOT="${2:-}"
        if [[ -z "$SSD_ROOT" ]]; then
          echo "--ssd-root requires a path." >&2
          exit 1
        fi
        shift 2
        ;;
      --duplicate-scan-root)
        DUPLICATE_SCAN_ROOT="${2:-}"
        if [[ -z "$DUPLICATE_SCAN_ROOT" ]]; then
          echo "--duplicate-scan-root requires a path." >&2
          exit 1
        fi
        shift 2
        ;;
      --quarantine-duplicates)
        QUARANTINE_DUPLICATES=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        usage >&2
        exit 1
        ;;
    esac
  done
}

capture_command() {
  local output_file="$1"
  shift
  log "CAPTURE: $* -> ${output_file}"
  if "$@" >"$output_file" 2>&1; then
    log "OK: captured ${output_file}"
  else
    log "WARN: command failed while capturing ${output_file}"
  fi
}

install_docker_if_requested() {
  if command -v docker >/dev/null 2>&1; then
    DOCKER_AVAILABLE=1
    log "Docker CLI found: $(command -v docker)"
    return
  fi

  DOCKER_AVAILABLE=0
  log "WARN: Docker CLI was not found."
  if [[ "$INSTALL_DOCKER" -ne 1 ]]; then
    log "Skipping Docker installation. Re-run with --install-docker if the NAS package manager should install it."
    return
  fi

  if command -v apt-get >/dev/null 2>&1; then
    run_cmd apt-get update
    run_cmd apt-get install -y docker.io docker-compose-plugin
  elif command -v apk >/dev/null 2>&1; then
    run_cmd apk add --no-cache docker docker-cli-compose
  elif command -v dnf >/dev/null 2>&1; then
    run_cmd dnf install -y docker docker-compose-plugin
  elif command -v yum >/dev/null 2>&1; then
    run_cmd yum install -y docker docker-compose-plugin
  else
    log "ERROR: no supported package manager found for automatic Docker installation."
    return
  fi

  if [[ "$APPLY" -eq 1 ]] && command -v docker >/dev/null 2>&1; then
    DOCKER_AVAILABLE=1
  fi
}

capture_host_baseline() {
  capture_command "${LOG_DIR}/host_uname_${TIMESTAMP}.txt" uname -a
  capture_command "${LOG_DIR}/host_disk_${TIMESTAMP}.txt" df -h
  capture_command "${LOG_DIR}/host_mounts_${TIMESTAMP}.txt" findmnt
  capture_command "${LOG_DIR}/host_memory_${TIMESTAMP}.txt" free -h
  if command -v lsblk >/dev/null 2>&1; then
    capture_command "${LOG_DIR}/host_lsblk_${TIMESTAMP}.txt" lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL
  fi
}

detect_protected_data_disk() {
  if ! command -v lsblk >/dev/null 2>&1; then
    log "WARN: lsblk unavailable; cannot detect protected 1.8 TB data disk."
    return
  fi

  local protected_disks
  protected_disks="$(lsblk -b -dn -o NAME,SIZE,TYPE,MODEL 2>/dev/null | awk -v min="$PROTECTED_DISK_MIN_BYTES" -v max="$PROTECTED_DISK_MAX_BYTES" '$3 == "disk" && $2 >= min && $2 <= max {print}' || true)"
  if [[ -z "$protected_disks" ]]; then
    log "No 1.7-1.9 TB data disk detected by lsblk."
    return
  fi

  PROTECTED_DISK_FOUND=1
  log "PROTECTED STORAGE DETECTED: one or more 1.7-1.9 TB disks match the do-not-touch rule."
  while IFS= read -r disk; do
    [[ -z "$disk" ]] && continue
    log "Protected disk: ${disk}"
  done <<< "$protected_disks"
  log "The script will not partition, format, fsck, mount, unmount, wipe, or prune Docker storage while this guard is active."
}

audit_network_connections() {
  if command -v ip >/dev/null 2>&1; then
    capture_command "${LOG_DIR}/network_ip_addr_${TIMESTAMP}.txt" ip addr show
    capture_command "${LOG_DIR}/network_ip_route_${TIMESTAMP}.txt" ip route show
  else
    log "WARN: ip command unavailable; skipping interface and route capture."
  fi
  if command -v ss >/dev/null 2>&1; then
    capture_command "${LOG_DIR}/network_listeners_${TIMESTAMP}.txt" ss -tulpn
  elif command -v netstat >/dev/null 2>&1; then
    capture_command "${LOG_DIR}/network_listeners_${TIMESTAMP}.txt" netstat -tulpn
  fi
  if command -v resolvectl >/dev/null 2>&1; then
    capture_command "${LOG_DIR}/network_dns_${TIMESTAMP}.txt" resolvectl status
  else
    capture_command "${LOG_DIR}/network_dns_${TIMESTAMP}.txt" cat /etc/resolv.conf
  fi

  local gateway
  if command -v ip >/dev/null 2>&1; then
    gateway="$(ip route show default 2>/dev/null | awk 'NR == 1 {print $3}' || true)"
  else
    gateway=""
  fi
  if [[ -n "$gateway" ]] && command -v ping >/dev/null 2>&1; then
    capture_command "${LOG_DIR}/network_gateway_ping_${TIMESTAMP}.txt" ping -c 1 -W 2 "$gateway"
  fi

  local host
  for host in "${PROBE_HOSTS[@]}"; do
    if command -v ping >/dev/null 2>&1; then
      capture_command "${LOG_DIR}/network_probe_${host//[^A-Za-z0-9_.-]/_}_${TIMESTAMP}.txt" ping -c 2 -W 2 "$host"
    fi
  done
}

audit_app_patterns() {
  if [[ "$DOCKER_AVAILABLE" -ne 1 ]]; then
    log "Docker unavailable; skipping app pattern audit."
    return
  fi

  capture_command "${LOG_DIR}/docker_volumes_${TIMESTAMP}.txt" docker volume ls
  while IFS= read -r volume_name; do
    [[ -z "$volume_name" ]] && continue
    capture_command "${LOG_DIR}/docker_volume_${volume_name}_${TIMESTAMP}.json" docker volume inspect "$volume_name"
  done < <(docker volume ls -q 2>/dev/null || true)

  capture_command "${LOG_DIR}/docker_images_${TIMESTAMP}.txt" docker images --digests
  capture_command "${LOG_DIR}/docker_container_mounts_${TIMESTAMP}.txt" docker ps -a --format "table {{.Names}}\t{{.Image}}\t{{.Mounts}}\t{{.Ports}}"
  capture_command "${LOG_DIR}/docker_container_restart_policies_${TIMESTAMP}.txt" docker inspect --format '{{.Name}} restart={{.HostConfig.RestartPolicy.Name}} health={{if .Config.Healthcheck}}yes{{else}}no{{end}} image={{.Config.Image}}' $(docker ps -aq 2>/dev/null || true)
}

scan_duplicates() {
  if [[ -z "$DUPLICATE_SCAN_ROOT" ]]; then
    log "Skipping duplicate scan. Use --duplicate-scan-root PATH to scan for duplicate data."
    return
  fi
  if [[ ! -d "$DUPLICATE_SCAN_ROOT" ]]; then
    log "WARN: duplicate scan root does not exist: ${DUPLICATE_SCAN_ROOT}"
    return
  fi

  local report_file="${LOG_DIR}/duplicates_${TIMESTAMP}.tsv"
  local files_file="${LOG_DIR}/duplicate_candidates_${TIMESTAMP}.txt"
  log "Scanning duplicates below ${DUPLICATE_SCAN_ROOT}; this is read-only unless --quarantine-duplicates and --apply are both set."
  find "$DUPLICATE_SCAN_ROOT" -type f -size +0c -print0 2>/dev/null \
    | xargs -0 sha256sum 2>/dev/null \
    | awk '{hash=$1; $1=""; sub(/^ /, ""); count[hash]++; paths[hash]=paths[hash] "\n" $0} END {for (h in count) if (count[h] > 1) printf "%s\t%d%s\n", h, count[h], paths[h]}' \
    > "$report_file"
  chmod 0640 "$report_file"
  log "Duplicate report written to ${report_file}."

  if [[ "$QUARANTINE_DUPLICATES" -ne 1 ]]; then
    log "Duplicate quarantine not requested; no files were moved or deleted."
    return
  fi
  if [[ "$APPLY" -ne 1 ]]; then
    log "Duplicate quarantine requires --apply; no files were moved or deleted."
    return
  fi
  if [[ -z "$SSD_ROOT" || ! -d "$SSD_ROOT" ]]; then
    log "Duplicate quarantine requires an existing --ssd-root path; no files were moved or deleted."
    return
  fi

  local quarantine_dir="${SSD_ROOT}/.ugreen-duplicate-quarantine/${TIMESTAMP}"
  mkdir -p "$quarantine_dir"
  awk 'BEGIN {current=""} /^([a-f0-9]{64})\t/ {current=$1; seen=0; next} /^\// {seen++; if (seen > 1) print}' "$report_file" > "$files_file"
  while IFS= read -r duplicate_path; do
    [[ -z "$duplicate_path" ]] && continue
    local target="${quarantine_dir}${duplicate_path}"
    mkdir -p "$(dirname "$target")"
    run_cmd mv -- "$duplicate_path" "$target"
  done < "$files_file"
  log "Duplicate quarantine completed into ${quarantine_dir}. No duplicate files were permanently deleted."
}

capture_docker_baseline() {
  if [[ "$DOCKER_AVAILABLE" -ne 1 ]]; then
    log "Docker unavailable; skipping Docker-specific baseline capture."
    return
  fi

  capture_command "${LOG_DIR}/docker_version_${TIMESTAMP}.txt" docker version
  capture_command "${LOG_DIR}/docker_info_${TIMESTAMP}.txt" docker info
  capture_command "${LOG_DIR}/docker_ps_${TIMESTAMP}.txt" docker ps -a
  capture_command "${LOG_DIR}/docker_df_${TIMESTAMP}.txt" docker system df
  capture_command "${LOG_DIR}/docker_network_ls_${TIMESTAMP}.txt" docker network ls
  capture_command "${LOG_DIR}/docker_container_ports_${TIMESTAMP}.txt" docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
  while IFS= read -r network_id; do
    [[ -z "$network_id" ]] && continue
    capture_command "${LOG_DIR}/docker_network_${network_id}_${TIMESTAMP}.json" docker network inspect "$network_id"
  done < <(docker network ls -q 2>/dev/null || true)

  if command -v journalctl >/dev/null 2>&1; then
    capture_command "${LOG_DIR}/docker_journal_${TIMESTAMP}.txt" journalctl -u docker --no-pager -n 300
  fi
}

configure_docker_daemon() {
  if [[ "$CONFIGURE_DAEMON" -ne 1 ]]; then
    log "Skipping Docker daemon log-rotation config because --no-daemon-config was set."
    return
  fi

  local docker_dir="/etc/docker"
  local daemon_file="${docker_dir}/daemon.json"
  local backup_file="${LOG_DIR}/daemon.json.${TIMESTAMP}.bak"
  local desired
  desired=$'{\n  "log-driver": "json-file",\n  "log-opts": {\n    "max-size": "10m",\n    "max-file": "3"\n  },\n  "live-restore": true\n}\n'

  if [[ -f "$daemon_file" && "$FORCE_DAEMON_CONFIG" -ne 1 ]]; then
    log "Existing ${daemon_file} detected; not replacing it. Use --force-daemon-config to back it up and replace it."
    return
  fi

  if [[ "$APPLY" -eq 1 ]]; then
    mkdir -p "$docker_dir"
    if [[ -f "$daemon_file" ]]; then
      cp -a "$daemon_file" "$backup_file"
      log "Backed up ${daemon_file} to ${backup_file}."
    fi
    printf '%s' "$desired" > "$daemon_file"
    chmod 0644 "$daemon_file"
    log "Wrote safe Docker daemon log-rotation config to ${daemon_file}."
  else
    log "DRY-RUN: create/update ${daemon_file} with json-file log rotation and live-restore."
  fi
}

restart_docker_service() {
  log "Checking Docker service manager."
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files docker.service >/dev/null 2>&1; then
    run_cmd systemctl reset-failed docker
    run_cmd systemctl enable docker
    run_cmd systemctl daemon-reload
    run_cmd systemctl restart docker
    run_cmd systemctl --no-pager --full status docker
  elif command -v service >/dev/null 2>&1; then
    run_cmd service docker restart
    run_cmd service docker status
  else
    log "WARN: no supported service manager found; skipping Docker service restart."
  fi

  if [[ "$APPLY" -eq 1 ]] && command -v docker >/dev/null 2>&1; then
    DOCKER_AVAILABLE=1
  fi
}

restart_unhealthy_containers() {
  if [[ "$DOCKER_AVAILABLE" -ne 1 ]]; then
    log "Docker unavailable; skipping unhealthy container restart."
    return
  fi
  if [[ "$RESTART_UNHEALTHY" -ne 1 ]]; then
    log "Skipping unhealthy container restart because --no-restart-unhealthy was set."
    return
  fi

  local containers
  containers="$(docker ps --filter 'health=unhealthy' --format '{{.Names}}' 2>/dev/null || true)"
  if [[ -z "$containers" ]]; then
    log "No unhealthy containers detected."
    return
  fi

  while IFS= read -r container; do
    [[ -z "$container" ]] && continue
    run_cmd docker inspect "$container"
    run_cmd docker restart "$container"
  done <<< "$containers"
}

compose_config_command() {
  if docker compose version >/dev/null 2>&1; then
    printf 'docker compose'
  elif command -v docker-compose >/dev/null 2>&1; then
    printf 'docker-compose'
  else
    return 1
  fi
}

validate_compose_files() {
  if [[ "$DOCKER_AVAILABLE" -ne 1 ]]; then
    log "Docker unavailable; skipping compose validation."
    return
  fi
  if [[ ! -d "$COMPOSE_ROOT" ]]; then
    log "Compose root does not exist: ${COMPOSE_ROOT}. Skipping compose validation."
    return
  fi

  local compose_cmd
  if ! compose_cmd="$(compose_config_command)"; then
    log "WARN: neither 'docker compose' nor 'docker-compose' is available; skipping compose validation."
    return
  fi

  log "Searching compose files below ${COMPOSE_ROOT}."
  while IFS= read -r -d '' compose_file; do
    local compose_report="${LOG_DIR}/compose_$(basename "$(dirname "$compose_file")")_${TIMESTAMP}.txt"
    log "Validating compose file: ${compose_file}"
    if [[ "$compose_cmd" == "docker compose" ]]; then
      if docker compose -f "$compose_file" config >"$compose_report" 2>&1; then
        log "OK: ${compose_file}"
      else
        log "WARN: compose validation failed for ${compose_file}"
      fi
    else
      if docker-compose -f "$compose_file" config >"$compose_report" 2>&1; then
        log "OK: ${compose_file}"
      else
        log "WARN: compose validation failed for ${compose_file}"
      fi
    fi
  done < <(find "$COMPOSE_ROOT" -type f \( -name 'docker-compose.yml' -o -name 'docker-compose.yaml' -o -name 'compose.yml' -o -name 'compose.yaml' \) -print0 2>/dev/null)
}

apply_kernel_tuning() {
  local sysctl_file="/etc/sysctl.d/99-ugreen-docker.conf"
  local backup_file="${LOG_DIR}/99-ugreen-docker.conf.${TIMESTAMP}.bak"
  local desired
  desired=$'# Managed by ugreen_docker_repair_optimize.sh\nnet.core.somaxconn = 1024\nnet.ipv4.ip_local_port_range = 1024 65000\nfs.inotify.max_user_watches = 524288\nfs.inotify.max_user_instances = 1024\n'

  if [[ "$APPLY" -eq 1 ]]; then
    if [[ -f "$sysctl_file" ]]; then
      cp -a "$sysctl_file" "$backup_file"
      log "Backed up ${sysctl_file} to ${backup_file}."
    fi
    log "Writing Docker-friendly sysctl tuning to ${sysctl_file}."
    printf '%s' "$desired" > "$sysctl_file"
    sysctl --system 2>&1 | tee -a "$RUN_LOG"
  else
    log "DRY-RUN: write Docker-friendly sysctl tuning to ${sysctl_file}."
  fi
}

prune_docker() {
  if [[ "$PRUNE_REQUESTED" -eq 1 ]]; then
    log "REFUSING docker system prune: data-disk safety policy is active."
    log "No Docker data is deleted by this toolkit because the 1.8 TB disk must not be touched."
  else
    log "Skipping docker prune. This toolkit does not delete Docker data automatically."
  fi
}

create_management_report() {
  local report_file="${LOG_DIR}/ugreen_docker_management_report_${TIMESTAMP}.md"
  local mode docker_available protected_disk
  mode="$([[ "$APPLY" -eq 1 ]] && echo apply || echo dry-run)"
  docker_available="$([[ "$DOCKER_AVAILABLE" -eq 1 ]] && echo yes || echo no)"
  protected_disk="$([[ "$PROTECTED_DISK_FOUND" -eq 1 ]] && echo yes || echo no)"

  cat > "$report_file" <<REPORT
# UGREEN DXP4800 Docker Management Audit

- Mode: ${mode}
- Docker CLI available: ${docker_available}
- Protected 1.7-1.9 TB disk detected: ${protected_disk}
- Data deletion policy: Docker prune and disk mutation are refused by this toolkit.
- Compose root: ${COMPOSE_ROOT}
- SSD root: ${SSD_ROOT:-not set}
- Duplicate scan root: ${DUPLICATE_SCAN_ROOT:-not set}

## What was checked

- Host kernel, memory, mounts, block devices, and disk usage
- NAS network interfaces, routes, DNS, listeners, gateway, and optional probe hosts
- Docker version, info, containers, storage usage, networks, network inspect output, volumes, mounts, restart policies, healthchecks, and published ports when Docker is available
- Duplicate candidates when --duplicate-scan-root is provided; duplicates are only quarantined to SSD when explicitly requested with --apply
- Compose files under the configured compose root when Docker and Compose are available

Review the timestamped artifacts in ${LOG_DIR} for raw command output.
REPORT
  chmod 0640 "$report_file"
  log "Wrote Docker management audit summary: ${report_file}"
}

final_report() {
  if [[ "$DOCKER_AVAILABLE" -eq 1 ]]; then
    capture_command "${LOG_DIR}/docker_ps_after_${TIMESTAMP}.txt" docker ps -a
    capture_command "${LOG_DIR}/docker_df_after_${TIMESTAMP}.txt" docker system df
  fi
  log "Done. Review logs in ${LOG_DIR}. Main log: ${RUN_LOG}"
}

main() {
  parse_args "$@"
  require_root
  prepare_log_dir
  log "Starting ${SCRIPT_NAME}. Mode: $([[ "$APPLY" -eq 1 ]] && echo apply || echo dry-run)."
  capture_host_baseline
  detect_protected_data_disk
  audit_network_connections
  install_docker_if_requested
  capture_docker_baseline
  audit_app_patterns
  scan_duplicates
  validate_compose_files
  configure_docker_daemon
  restart_docker_service
  restart_unhealthy_containers
  apply_kernel_tuning
  prune_docker
  create_management_report
  final_report
}

main "$@"
