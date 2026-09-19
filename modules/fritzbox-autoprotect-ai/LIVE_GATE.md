# Block-0 local live gate

The code gate and the network gate are separate.

GitHub CI may test source code, but it must not be a remote execution channel into the home network. The Block-0 live gate therefore runs **only on the NAS/local management node**.

## Privacy contract

`live_gate.py` prints only:

- score and gate result
- critical blocker names
- FRITZ!Box reachable yes/no
- host count and inventory-complete yes/no
- port-mapping count and scan-complete yes/no
- remote-management scan completeness
- number of USP controllers
- error count
- mutation-gate state

It never prints host names, IP addresses, MAC addresses, serial numbers, port numbers, controller hostnames, passwords or tokens.

The current full snapshot is local-only and written with mode 0600. The state directory is forced to 0700.

## Run

```bash
sudo -u aegis /opt/aegis/fritzbox-autoprotect-ai/.venv/bin/python \
  /opt/aegis/fritzbox-autoprotect-ai/live_gate.py \
  --config /etc/aegis/fritzbox-autoprotect.toml
```

Exit code 0 means the Block-0 runtime gate passed. Exit code 20 means it remains locked.

## Gate rules

A numeric score alone is insufficient. Every critical Block-0 check must also be PASS:

- local control plane reachable
- remote-management scan complete and no disallowed exposure detected
- WAN port-mapping scan complete
- host inventory scan complete
- audit chain valid

UNKNOWN critical telemetry is a blocker.

A passing source CI never substitutes for this local test.
