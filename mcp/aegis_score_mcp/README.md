# AEGIS Score Evidence MCP

Read-only MCP bridge for AEGIS score evidence. It exposes only sanitized score/status metadata and keeps the distinction between real evidence, verified exports, fixtures, synthetic data, heuristics, targets, templates, archive claims and unknown evidence.

## Sources

The server scans three local roots:

- `AEGIS_UGREEN_REPO`: clone/sync of `thepointer1982-maker/Ugreen-server`
- `AEGIS_OPENJARVIS_REPO`: clone/sync of `thepointer1982-maker/OpenJarvis-main`
- `AEGIS_DRIVE_HUB`: locally mounted/synced `00_KI-Agenten_Datenhub`

The Drive source is deliberately a local mount. The MCP server itself stores no Google OAuth token and does not require a cloud API credential.

## MCP tools

- `aegis_score_sources`: source availability only.
- `aegis_score_changes`: current score/status scan plus baseline diff, freshness, provenance, manifest/hash verification and alerts.
- `aegis_score_commit_baseline`: writes only the sanitized comparison baseline into `AEGIS_SCORE_STATE_DIR`.

Resource `aegis://scores/evidence-policy` exposes the evidence rules.

## Evidence rules

`REAL_DEVICE_MEASUREMENT` is accepted only when explicitly classified and accompanied by device/observation metadata. `VERIFIED_EXPORT` is accepted only when the artifact hash matches `manifest.sha256` and an export-session identifier exists.

`TEST_FIXTURE`, `SYNTHETIC`, `HEURISTIC`, `PROJECTED_TARGET`, `TEMPLATE` and `ARCHIVE_CLAIM` never count as real improvement. `UNKNOWN` is also non-countable.

Freshness defaults to 168 hours and can be changed with `AEGIS_SCORE_FRESHNESS_HOURS`. Future timestamps, stale evidence and hash mismatches generate warnings. Test/synthetic changes never trigger `notification_recommended`.

## Safety boundary

The scanner never changes devices, Docker, router settings, firmware, user accounts, GitHub source files or Drive source files. It does not return raw logs. The only write operation is the sanitized baseline file under `AEGIS_SCORE_STATE_DIR`.

The example configuration uses MCP stdio. It opens no listening TCP port.

## Install

Python 3.10+ is required. The current official MCP Python SDK v2 line is used:

```bash
python3 -m venv /opt/aegis/venvs/score-mcp
/opt/aegis/venvs/score-mcp/bin/pip install -r /opt/aegis/Ugreen-server/mcp/aegis_score_mcp/requirements.txt
```

For a local MCP host, copy the settings from `mcp-config.example.json` and adapt only filesystem paths.

## Recommended run sequence

1. Call `aegis_score_sources`.
2. Call `aegis_score_changes`.
3. Notify only when `notification_recommended=true`; inspect evidence class and provenance before treating a change as real.
4. After verification, call `aegis_score_commit_baseline` once so the next run reports only changes.

Do not commit the baseline before reviewing integrity/provenance alerts; that intentionally accepts the current sanitized state as the next comparison point.
