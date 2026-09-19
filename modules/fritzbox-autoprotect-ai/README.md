# AEGIS fritzbox-autoprotect-ai

Block 0 implements a local-first home-network security observer for AEGIS.

Current behavior is intentionally **observe-only by default**:

- discover FRITZ!Box TR-064 services from the LAN
- inventory hosts and WAN port mappings
- probe advertised no-input getters for RemoteAccess, MyFRITZ, TR-069 and USP/TR-369
- compute a deterministic quality/security score
- keep a SHA-256 hash-chain audit ledger
- select installed local Ollama models according to task and memory budget
- expose status, observation, model-routing and mutation-gate tools over MCP
- reject public/WAN FRITZ!Box control endpoints

No router mutation is implemented in Block 0. The apply gate is closed by default and later blocks must prove backups, rollback and stable scores before reversible changes are added.

## Quick test

```bash
cd modules/fritzbox-autoprotect-ai
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

## First read-only cycle

```bash
export AEGIS_FRITZ_PASSWORD='set-at-runtime-only'
python block0.py --config config.example.toml once
```

Never commit the FRITZ!Box password. Production secrets belong in a local secret store or root/service-readable environment file.

## MCP

The project targets MCP 2026-07-28 via the Python SDK 2.x.

```bash
pip install -e '.[mcp]'
mcp dev mcp_server.py
```

For a deployed HTTP MCP transport, bind only to localhost or a trusted management network and add explicit authentication.

See `ROADMAP.md` for the gated path from this foundation to the broader AEGIS/Jarvis-style system.