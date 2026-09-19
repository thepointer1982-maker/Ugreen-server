# AEGIS AutoProtect – Score-gated roadmap

A block is complete only when its code, measurements and rollback evidence satisfy its gate without regressing earlier blocks.

| Block | Capability | Gate |
|---|---|---:|
| 0 | Local control plane, read-only TR-064 inventory, deterministic score, audit chain, MCP skeleton | 70 |
| 1 | Full FRITZ inventory: hosts, services, WAN mappings, remote-management state, device identity baseline | 80 |
| 2 | Reversible exposure hardening for RemoteAccess/MyFRITZ/TR-069/USP and explicit port mappings | 85 |
| 3 | Local DNS/privacy layer, tracker blocking, per-device allowlists, TV/app regression checks | 90 |
| 4 | Signed device mesh, Wake-on-LAN, heartbeats, NAS/Mac/Windows worker roles | 92 |
| 5 | Multi-model brain: local Ollama/DeepSeek plus optional Kimi/Codex adapters | 94 |
| 6 | Smart-home skills and bounded automations for media, cameras and supported home devices | 95 |
| 7 | Observer/shadow core, canary repair, restore engine, loop detector and fault injection | 96 |
| 8 | Jarvis-like UX: voice, dashboard, natural-language projects and proactive bounded suggestions | 97 |
| 9 | Production: reproducible install, signed releases, failover/restore drills and long-run quality | 98 |

## Autonomous loop

1. Observe immutable facts.
2. Normalize state and compare it with the last known-good baseline.
3. Detect anomalies and attach confidence/severity.
4. Ask a local model for explanations and candidate plans only.
5. Convert a plan into explicit deterministic actions with preconditions and rollback.
6. Dry-run/simulate.
7. Check score, audit integrity and stable-cycle gate.
8. Canary the smallest reversible change.
9. Verify router, DNS, device and user-visible health.
10. Commit or rollback automatically.
11. Learn only from verified outcomes.

## Hard invariants

- LAN-only router control by default.
- No credentials in Git, logs, prompts or MCP responses.
- No model receives direct administrative credentials.
- Models propose; deterministic tools enforce.
- No automatic factory reset, firmware flash, WAN management enablement, mass deletion or irreversible firewall change.
- Local models are the production default. Remote Kimi/Codex/cloud adapters remain disabled unless explicitly enabled.
- Unknown telemetry never scores as a clean PASS.
