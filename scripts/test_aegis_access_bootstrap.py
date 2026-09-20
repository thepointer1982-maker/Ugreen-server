#!/usr/bin/env python3
from __future__ import annotations
import os
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "aegis_access_bootstrap.sh"

def main():
    p = subprocess.run(["bash", str(SCRIPT), "--help"], text=True, capture_output=True)
    assert p.returncode == 0
    assert "never open router ports" in p.stdout
    assert "authenticated GitHub CLI" in p.stdout

    with tempfile.TemporaryDirectory() as raw:
        fakebin = Path(raw)
        gh = fakebin / "gh"
        gh.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
        gh.chmod(0o755)
        env = os.environ.copy()
        env.pop("AEGIS_RUNNER_TOKEN", None)
        env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
        p = subprocess.run(["bash", str(SCRIPT), "preflight"], text=True, capture_output=True, env=env)
        assert p.returncode == 0
        assert "github_cli_auth=unavailable" in p.stdout
        assert "gh=ok" in p.stdout

    text = SCRIPT.read_text(encoding="utf-8")
    assert "gh_runner_capable" in text
    assert "gh auth status --hostname github.com" in text
    assert "aegis_runner_install.sh" in text
    assert "aegis_tailscale_bridge.sh" in text
    assert "tailscale ip -4" in text
    assert "ssh -p" in text
    print("AEGIS ACCESS BOOTSTRAP TESTS PASS")

if __name__ == "__main__":
    main()
