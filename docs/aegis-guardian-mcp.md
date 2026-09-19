# AEGIS Guardian + MCP

This layer keeps AEGIS local-first and fail-closed.

## Guardian

Run a read-only/measurement cycle:

```bash
python3 scripts/aegis_guardian_cycle.py
```

Allow only the built-in reversible repair allowlist:

```bash
python3 scripts/aegis_guardian_cycle.py --repair
```

The current allowlist is intentionally narrow:

- reset/start only the user service `aegis-export.service`

Scheduler lock recovery is handled only by `aegis_scheduled_run.sh`, which
validates PID + boot-id ownership before recovering a stale lock.

Partitioning, formatting, EFI/bootloader writes, Windows changes, and internet exposure are never performed.

State is written below `~/.local/state/aegis-guardian/`.
Repair transitions become append-only learning cards in `learning-cards.jsonl`.

## MCP v2

The MCP server uses the official Python SDK v2 API:

```bash
uv run --with "mcp[cli]>=2,<3" mcp run scripts/aegis_mcp_server.py
```

It exposes:

- resource `aegis://status`
- resource `aegis://local-ai`
- resource `aegis://model-matrix`
- resource `aegis://learning-gate`
- resource `aegis://learning-cards`
- tool `guardian_status`
- tool `local_ai_inventory`
- tool `model_agent_matrix`
- tool `learning_gate_status`
- tool `learning_cards`
- tool `run_guardian_cycle(repair=false)`

The repair tool cannot execute arbitrary shell commands. It delegates only to the hard-coded guardian repair allowlist.


## Learning lifecycle

Accepted learning candidates are not activated immediately.

1. The learning gate verifies signed baseline/candidate evidence.
2. The candidate enters a single exclusive provisional probation state.
3. Guardian health cycles provide measured pass/fail observations.
4. Only a confirmed candidate becomes last-known-good.
5. Activation is atomic and only targets an operator-configured local path.
6. The previous active artifact is hash-verified and backed up first.
7. The new active state enters `validating`.
8. Guardian health cycles promote it to `stable` or roll back to the previous active artifact.

Concurrent provisional candidates and concurrent validating activations are blocked. Re-submitting the same candidate/activation is idempotent.

Repository CI validates the state machine, provenance, LKG, rollback, activation, MCP fail-closed contracts, and regression tests. A green repository state does not by itself prove deployment on the physical NAS or worker nodes.
