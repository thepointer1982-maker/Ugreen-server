# fritzbox-autoprotect-ai skill

## Purpose
Local-first AEGIS skill for FRITZ!Box observation, exposure scoring, audited planning and later reversible protection.

## Required cycle
Observe -> normalize -> score -> plan -> dry-run -> gate -> canary -> verify -> commit/rollback -> learn.

## Block 0 tools
- aegis_status
- aegis_fritzbox_observe
- aegis_route_model
- aegis_mutation_gate

## Safety contract
- Router endpoint must be local/private.
- apply_enabled defaults to false.
- Mutations require explicit apply mode, minimum score, multiple stable cycles and a valid audit chain.
- Secrets are runtime-only.
- Unknown scan completeness is UNKNOWN, never PASS.
- Local models are preferred; remote fallback defaults to off.
