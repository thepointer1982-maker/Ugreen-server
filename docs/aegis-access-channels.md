# AEGIS access channels

AEGIS remains local-first. Remote maintenance channels are optional and must not
replace the LAN-only production posture.

## Priority 1: LAN SSH

Use the NAS SSH service on port 22 from a device already inside the home LAN.

- keep "local network only" enabled
- keep router port forwarding disabled
- prefer an SSH key over a reusable password
- run the tested local entrypoint:

```bash
cd /path/to/Ugreen-server
bash scripts/aegis_remote_apply_retry.sh --repo-root "$(pwd)" --repair
```

## Priority 2: Tailscale maintenance bridge

Files:

- `deploy/tailscale/docker-compose.yml`
- `scripts/aegis_tailscale_bridge.sh`

The bridge is opt-in and uses an outbound Tailscale connection. No public
Tailscale Funnel is configured.

The container uses the host network namespace and forwards only a private
tailnet TCP port to the existing NAS SSH daemon on `127.0.0.1:22`.

```bash
export TS_AUTHKEY='<one-time-or-reusable-key-from-tailscale>'
bash scripts/aegis_tailscale_bridge.sh start
unset TS_AUTHKEY
bash scripts/aegis_tailscale_bridge.sh serve-ssh
bash scripts/aegis_tailscale_bridge.sh status
```

The default tailnet SSH forwarding port is `2222`. Stop the maintenance
channel when it is not needed:

```bash
bash scripts/aegis_tailscale_bridge.sh reset-serve
bash scripts/aegis_tailscale_bridge.sh stop
```

The Tailscale container image is version- and digest-pinned. The auth key is
never stored in the repository.

## Priority 3: GitHub self-hosted NAS control runner

Files:

- `.github/workflows/aegis-nas-control.yml`
- `scripts/aegis_runner_install.sh`
- branch `aegis-control`
- request file `.aegis-control/request.json`

This is an outbound-only control path from the NAS to GitHub Actions.

The workflow:

- only runs on `aegis-control`
- only reacts to changes of the request file
- requires the repository owner as `github.actor`
- targets only the custom runner label `aegis-ugreen`
- does not execute pull-request code
- accepts only `status`, `real-cycle`, or `deploy-retry`
- executes the already-installed trusted local repository on the NAS

One-time registration requires a short-lived repository runner registration
token in the local shell:

```bash
export AEGIS_RUNNER_TOKEN='<short-lived-registration-token>'
bash scripts/aegis_runner_install.sh
unset AEGIS_RUNNER_TOKEN
```

After registration, new allowlisted requests can be issued by updating
`.aegis-control/request.json` on the `aegis-control` branch.

## Priority 4: Vendor browser access

UGREENLink can remain a manual emergency/browser path. It is not considered a
trusted automation transport for AEGIS and is intentionally separate from the
local production core.

## Never do this for convenience

- do not expose NAS SSH directly to the public Internet
- do not add router port forwarding for port 22
- do not place passwords, Tailscale auth keys, runner registration tokens, or
  private SSH keys in Git
- do not execute arbitrary commands from control requests
- do not run public pull-request workflows on the self-hosted NAS runner
