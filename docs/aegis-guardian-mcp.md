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

- remove an empty scheduler lock older than two hours
- reset/start only the user service `aegis-export.service`

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
- resource `aegis://learning-cards`
- tool `guardian_status`
- tool `learning_cards`
- tool `run_guardian_cycle(repair=false)`

The repair tool cannot execute arbitrary shell commands. It delegates only to the hard-coded guardian repair allowlist.
