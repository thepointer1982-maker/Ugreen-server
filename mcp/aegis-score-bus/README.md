# AEGIS Score Bus MCP

Read-only MCP server for AEGIS score/evidence inspection.

## Security model

- no router, firmware, account or device mutation tools
- no outbound network required
- Docker example uses `network_mode: none`
- evidence roots are mounted read-only
- path traversal is blocked
- unknown/test/synthetic/projected data is never promoted to real evidence

Only these evidence classes may support a real operational claim:

- `REAL_DEVICE_MEASUREMENT`
- `VERIFIED_EXPORT`

All other classes remain non-operational evidence:
`TEST_FIXTURE`, `SYNTHETIC`, `HEURISTIC`, `PROJECTED_TARGET`, `TEMPLATE`, `ARCHIVE_CLAIM`, `UNKNOWN`.

## Tools

### aegis_sources_status
Checks configured roots and canonical expected files.

### aegis_score_snapshot
Scans JSON/JSONL and extracts score/status-like fields while preserving evidence class.

### aegis_verify_manifest
Verifies SHA-256 manifest entries against evidence files.

### aegis_evidence_read
Reads one bounded text/JSON evidence file and returns modification time + SHA-256.

## Install

Requires Node.js 22+.

```bash
cd mcp/aegis-score-bus
npm install
npm run build
npm start
```

The server uses stdio, which is the safest default for a single local MCP host.

## Environment

```bash
export AEGIS_UGREEN_REPO=/srv/aegis/repos/Ugreen-server
export AEGIS_OPENJARVIS_REPO=/srv/aegis/repos/OpenJarvis-main
export AEGIS_DRIVE_HUB=/srv/aegis/drive/00_KI-Agenten_Datenhub
```

## Recommended UGREEN deployment

Use `compose.example.yml` as a template. Keep all source mounts `:ro` and keep `network_mode: none` unless a later reviewed requirement explicitly needs outbound access.

## Evidence rule

A numeric value is not a live AEGIS score merely because it exists in a JSON file. A live claim requires current provenance, timestamp/run identity, and either real-device evidence or a verified export chain. Manifest verification should be performed when `scores/manifest.sha256` exists.

## MCP SDK

This package targets the stable v2 TypeScript SDK line (`@modelcontextprotocol/server`) implementing the 2026-07-28 MCP specification.
