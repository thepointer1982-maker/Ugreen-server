#!/usr/bin/env python3
from __future__ import annotations
import os
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "aegis_runner_install.sh"
WORKFLOW = SCRIPT.parent.parent / ".github" / "workflows" / "aegis-nas-control.yml"

def main():
    p = subprocess.run(["bash", str(SCRIPT), "--help"], text=True, capture_output=True)
    assert p.returncode == 0
    assert "existing authenticated GitHub CLI session via gh api" in p.stdout
    assert "token is never printed or written" in p.stdout
    assert "aegis-ugreen-v2" in p.stdout

    with tempfile.TemporaryDirectory() as raw:
        fakebin = Path(raw)
        gh = fakebin / "gh"
        gh.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
        gh.chmod(0o755)
        env = os.environ.copy()
        env.pop("AEGIS_RUNNER_TOKEN", None)
        env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
        p = subprocess.run(["bash", str(SCRIPT)], text=True, capture_output=True, env=env)
        assert p.returncode == 4
        assert "No runner registration token available" in p.stderr

    s = SCRIPT.read_text(encoding="utf-8")
    assert "gh auth status --hostname github.com" in s
    assert "actions/runners/registration-token" in s
    assert "--jq '.token'" in s
    assert "RUNNER_TOKEN=\"\"" in s

    w = WORKFLOW.read_text(encoding="utf-8")
    assert "runs-on: [self-hosted, linux, aegis-ugreen-v2]" in w
    assert "cancel-in-progress: true" in w
    assert "github.actor == 'thepointer1982-maker'" in w
    assert "pull_request:" not in w
    assert 'allowed = {"status", "real-cycle", "deploy-retry"}' in w
    assert "trusted_sha" in w
    assert "merge-base --is-ancestor" in w
    assert "aegis/resume-pre-lenovo-20260920" in w
    assert "aegis/guardian-learning-mcp" not in w
    assert 'git -C "$REPO" clean -fdx' in w
    assert "AEGIS_REPO_PATH=$REPO" in w
    assert "curl " not in w
    print("AEGIS RUNNER CONTROL TESTS PASS")

if __name__ == "__main__":
    main()
