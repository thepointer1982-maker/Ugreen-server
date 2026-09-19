# fritzbox-autoprotect-ai skill

## Purpose
Local-first AEGIS skill for FRITZ!Box observation, exposure scoring, audited planning and later reversible protection.

## Required cycle
Observe -> normalize -> validate completeness -> score -> plan -> dry-run -> gate -> canary -> verify -> commit/rollback -> learn.

## Block 0 tools
- aegis_status
- aegis_fritzbox_observe
- aegis_route_model
- aegis_mutation_gate
- aegis_self_check

## Hard safety contract
- Router control endpoints must be private/local.
- Arbitrary DNS hostnames, external redirects and external descriptor URLs are rejected.
- HTTP(S) sessions ignore environment proxy variables.
- apply_enabled defaults to false.
- Block 0 contains no router mutation actions.
- Critical UNKNOWN states block progression.
- Caller-supplied scores never open a mutation gate.
- Full LAN inventory stays local in 0600 state files.
- CLI/MCP return only privacy-safe summaries.
- Audit append stops if an existing chain cannot be verified.
- HMAC-SHA256 is required before any future autonomous mutation can open.
- Secrets are runtime-only and never enter Git, prompts, MCP results or CI logs.
- Local Ollama is preferred; remote model fallback defaults to off.
- No cloud-triggered self-hosted runner is used as the production home-network gate.
