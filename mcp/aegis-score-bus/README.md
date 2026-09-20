# AEGIS Score Bus MCP

Canonical read-only MCP server for AEGIS score/evidence reconciliation.

OpenJarvis is a client/consumer only. Score classification and comparison logic live here so there is one source of truth.

## Canonical tools

- `aegis_sources_status` — configured source availability.
- `aegis_score_snapshot` — score/status extraction, cumulative baseline diff, provenance/freshness/hash checks, conflicts, regressions and first real-device evidence.
- `aegis_verify_manifest` — SHA-256 manifest verification.
- `aegis_evidence_read` — bounded evidence metadata only. Raw file/log contents are never returned.

The resource `aegis://scores/evidence-policy` exposes the evidence rules.

## Sources

- `AEGIS_UGREEN_REPO`: local clone/sync of `thepointer1982-maker/Ugreen-server`
- `AEGIS_OPENJARVIS_REPO`: local clone/sync of `thepointer1982-maker/OpenJarvis-main`
- `AEGIS_DRIVE_HUB`: local mount/sync of `00_KI-Agenten_Datenhub`

The Drive source is a local filesystem root. This MCP server stores no Google OAuth credential.

## Evidence contract

Evidence classes are strict:

`REAL_DEVICE_MEASUREMENT`, `VERIFIED_EXPORT`, `TEST_FIXTURE`, `SYNTHETIC`, `HEURISTIC`, `PROJECTED_TARGET`, `TEMPLATE`, `ARCHIVE_CLAIM`, `UNKNOWN`.

Only current, provenance-valid `REAL_DEVICE_MEASUREMENT` and `VERIFIED_EXPORT` records can count as verified operational changes.

A `VERIFIED_EXPORT` requires a matching `manifest.sha256` entry plus an export-session identifier. A real device measurement requires explicit classification plus device/observation metadata.

A new real value is not automatically an improvement. `real_improvement_count` only increases for a verified positive numeric delta against the previous baseline.

Test fixtures, synthetic values, heuristics, targets, templates, archive claims and unknown provenance never count as real improvements.

## Notification behavior

`notification_recommended` is true only for a new/changed verified operational record, a new/changed verified conflict, a reportable integrity/regression alert, or disappearance of previously verified evidence.

A persistent unchanged conflict remains visible in `conflicts` but is not re-notified after the baseline has advanced.

## Baseline activation

For the first activation after an already verified external checkpoint, call:

`aegis_score_snapshot(initialize_baseline=true)`

This records the current sanitized state and suppresses duplicate "new" notifications for evidence that has already been reviewed. Normal subsequent calls use the cumulative baseline and can update it automatically.

## Safety boundary

- read-only source roots
- no device, Docker, router, firmware, account or network-configuration mutation
- no raw private-log publication
- path traversal blocked for evidence inspection
- stdio transport; no listening TCP port
- only `AEGIS_SCORE_STATE_DIR` is written, for the sanitized cumulative comparison baseline

## Install

Python 3.10+:

```bash
python3 -m venv /srv/aegis/venvs/score-bus
/srv/aegis/venvs/score-bus/bin/pip install -r /srv/aegis/repos/Ugreen-server/mcp/aegis-score-bus/requirements.txt
```

Use `mcp-config.example.json` as the client configuration and adapt only local filesystem paths.

## Verification

The engine test suite covers verified exports, fixture suppression, unchanged-baseline silence, hash mismatch, conflicting verified scores, first real-device evidence, disappearance of verified evidence, same-value provenance changes, positive numeric improvement and suppression of repeated unchanged-conflict notifications.
