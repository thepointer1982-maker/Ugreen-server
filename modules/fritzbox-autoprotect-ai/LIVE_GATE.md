# Block-0 live gate

The code gate is not the network gate. Block 1 must remain locked until a real LAN-side run reaches the Block-0 threshold.

## Privacy rule

The self-hosted workflow runs on a Linux runner inside the home network. It must never upload the raw FRITZ!Box snapshot, host names, IP addresses, serial numbers or port-mapping details to GitHub.

`live_gate.py` therefore prints only:

- numeric score and gate result
- FRITZ!Box reachable yes/no
- host count and scan-complete yes/no
- port-mapping count and scan-complete yes/no
- error count
- mutation gate state/reason

The complete snapshot and audit record stay only in the local runner state directory.

## Credentials

Do not store the FRITZ!Box password as a repository secret for this local-first deployment.

If authentication is needed, configure it locally on the NAS runner in:

`/etc/aegis/fritzbox-autoprotect.env`

with permissions restricted to the runner/AEGIS service account. Example variable names:

`AEGIS_FRITZ_USER`
`AEGIS_FRITZ_PASSWORD`

The workflow sources the local file without printing it.

## Gate

Block 0 runtime gate: score >= 70.

A passing code CI does not satisfy this gate. A real read-only FRITZ!Box cycle from the home network is required.
