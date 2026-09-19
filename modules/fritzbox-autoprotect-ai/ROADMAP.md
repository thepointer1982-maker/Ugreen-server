# AEGIS AutoProtect – score-gated roadmap

A block is complete only when its implementation, measurements, privacy constraints and rollback evidence satisfy its gate without regressing earlier blocks.

| Block | Capability | Gate |
|---|---|---:|
| 0 | Hardened local control plane, read-only TR-064 inventory, fail-closed scoring, keyed-ready audit, MCP summaries | 70 |
| 1 | Full FRITZ inventory: hosts, services, WAN mappings, remote-management state, device identity baseline | 80 |
| 2 | Reversible exposure hardening for RemoteAccess/MyFRITZ/TR-069/USP and explicit port mappings | 85 |
| 3 | Local DNS/privacy layer, tracker blocking, per-device allowlists, TV/app regression checks | 90 |
| 4 | Signed device mesh, Wake-on-LAN, heartbeats, NAS/Mac/Windows worker roles | 92 |
| 5 | Multi-model brain: local Ollama/DeepSeek plus optional explicitly enabled adapters | 94 |
| 6 | Smart-home skills and bounded automations for media, cameras and supported home devices | 95 |
| 7 | Observer/shadow core, canary repair, restore engine, loop detector and fault injection | 96 |
| 8 | Jarvis-like UX: voice, dashboard, natural-language projects and proactive bounded suggestions | 97 |
| 9 | Production: reproducible install, signed releases, failover/restore drills and long-run quality | 98 |

## Autonomous loop

1. Observe immutable/read-only facts.
2. Validate scan completeness.
3. Normalize state and compare with the last verified baseline.
4. Detect anomalies and attach confidence/severity.
5. Let a model propose explanations/plans only.
6. Convert plans to deterministic actions with explicit preconditions and rollback.
7. Dry-run/simulate.
8. Require numeric score **and** all critical checks PASS.
9. Require an intact keyed audit and fresh stable cycles before mutation.
10. Canary the smallest reversible change.
11. Verify network, device and user-visible health.
12. Commit or rollback.
13. Learn only from verified outcomes.

## Hard invariants

- LAN-only production control.
- No credentials or raw LAN inventory in Git/CI/MCP output.
- No model gets administrative credentials.
- Models propose; deterministic tools enforce.
- No automatic factory reset, firmware flash, WAN-management enablement, mass deletion or irreversible firewall change.
- Local models are the production default.
- Critical UNKNOWN telemetry never passes.
- Audit state is append-only, verified before append, and future mutation requires HMAC-SHA256.
- Source CI is external; production live gates are local.
