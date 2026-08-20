# AEGIS scores

This directory receives sanitized local AEGIS exports.

Expected files:
- `latest.json` — current aggregate network/device score snapshot
- `deepdiag.json` — current deep diagnostics snapshot
- `history.jsonl` — append-only sanitized score history
- `manifest.sha256` — hashes for exported score artifacts

Do not commit secrets, credentials, tokens, raw private keys, or unrestricted system logs.
