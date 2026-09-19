# AEGIS fritzbox-autoprotect-ai

Local-first, cost-free AEGIS security observer for the home network. Production operation is LAN-only and does not require a paid API or cloud model.

## Block 0 / Hardening R1

The default is intentionally read-only:

- discover FRITZ!Box TR-064 services locally
- inventory hosts and WAN port mappings
- inspect RemoteAccess, MyFRITZ, ManagementServer/TR-069 and USP/TR-369
- enumerate configured USP controllers where supported
- score only verified facts; critical UNKNOWN states block the gate
- keep private raw state locally with directory mode 0700 and files mode 0600
- keep the audit log small: it stores a snapshot SHA-256 digest plus score/gate metadata, not the full host inventory
- optionally authenticate the audit chain with a local HMAC-SHA256 key
- return only redacted/count-only output through CLI and MCP
- reject public router endpoints, external redirects, external service URLs and environment proxies
- prefer local Ollama; remote model fallback is disabled by default
- run the service inside a restrictive systemd sandbox with private-network egress only

There are **no router mutation actions in Block 0**.

## Local setup

```bash
cd modules/fritzbox-autoprotect-ai
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

For development/security checks:

```bash
pip install -e '.[dev]'
pytest -q
ruff check block0.py live_gate.py mcp_server.py tests
bandit -q -r block0.py live_gate.py mcp_server.py
pip-audit -r requirements-audit.txt --progress-spinner off
```

All of these tools are free/open-source.

## Secrets and keyed audit

Never put FRITZ!Box credentials, API tokens, LAN snapshots or audit keys in Git.

Example local files:

```text
/etc/aegis/fritzbox-autoprotect.toml
/etc/aegis/fritzbox-autoprotect.env
/etc/aegis/autoprotect-audit.key
```

Recommended permissions:

```bash
sudo install -d -m 0700 -o aegis -g aegis /var/lib/aegis/autoprotect
sudo install -d -m 0750 -o root -g aegis /etc/aegis
sudo sh -c 'umask 077; head -c 32 /dev/urandom > /etc/aegis/autoprotect-audit.key'
sudo chown aegis:aegis /etc/aegis/autoprotect-audit.key
sudo chmod 0600 /etc/aegis/autoprotect-audit.key
```

The application deliberately rejects a group/world-accessible audit key. The production key is therefore owned by the dedicated `aegis` service account with mode 0600.

## First read-only cycle

```bash
export AEGIS_FRITZ_PASSWORD='runtime-only'
python block0.py --config config.example.toml once
python block0.py --config config.example.toml self-check
python block0.py --config config.example.toml verify-audit
```

CLI output is privacy-safe. The full current snapshot remains only under the configured local state directory.

## Local live gate

Run from the NAS itself:

```bash
python live_gate.py --config /etc/aegis/fritzbox-autoprotect.toml
```

Block 1 remains locked until a real LAN-side read-only run reaches the Block-0 threshold and all critical checks are PASS.

**Do not use a cloud-triggered self-hosted GitHub runner as the production live gate.** GitHub CI is used only for source-code tests.

## MCP

MCP tools return redacted summaries. The mutation-gate tool does not accept a caller-supplied score; it derives state from the latest locally audited cycle.

For development:

```bash
pip install -e '.[mcp]'
mcp dev mcp_server.py
```

For production, expose MCP only on localhost or a dedicated trusted management segment with explicit authentication.

See `SECURITY.md`, `LIVE_GATE.md`, and `ROADMAP.md`.
