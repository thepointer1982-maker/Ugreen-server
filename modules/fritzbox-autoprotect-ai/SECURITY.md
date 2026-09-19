# Security model

## Assets
- FRITZ!Box administrator credentials
- LAN host/IP/MAC inventory
- port-mapping data
- remote-management state
- AEGIS audit history
- local model endpoints

## Trust boundaries
1. FRITZ!Box / LAN control plane
2. AEGIS service account and local state
3. MCP clients
4. development CI
5. optional future worker nodes

Development CI is not trusted with home-network credentials or raw LAN data.

## Block-0 defenses
- no router mutation implementation
- private/local URL validation
- restricted hostname forms plus runtime private-address resolution
- no environment proxy inheritance
- redirects rejected
- TR-064 service/control URLs constrained to the configured FRITZ!Box origin
- bounded XML responses parsed with defusedxml
- validated SOAP action/argument names
- sensitive returned fields redacted
- raw state forced to 0700/0600
- symlink targets rejected for protected state
- atomic latest-state replacement and exclusive cycle lock
- audit verification before append
- optional HMAC-SHA256 keyed audit; required for future mutation
- latest snapshot bound to the last audit entry by SHA-256 digest
- critical UNKNOWN/failure blocks the gate
- MCP and CLI expose summary data only
- systemd egress restricted to private/local address ranges
- cloud-triggered home-network runner removed

## Non-goals in Block 0
Block 0 does not disable services, change port mappings, update firmware, alter DNS, install TV filters or repair endpoints. Those functions require later gates, backups and rollback proof.

## Incident behavior
If the audit chain is invalid, the key is unavailable after keyed entries exist, the current state cannot be bound to the audit, or critical telemetry is incomplete, autonomous mutation remains closed.
