#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "aegis_runner_install.sh"
WORKFLOW = SCRIPT.parent.parent / ".github" / "workflows" / "aegis-nas-control.yml"


def make_exe(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def main():
    p = subprocess.run(["bash", str(SCRIPT), "--help"], text=True, capture_output=True)
    assert p.returncode == 0
    assert "reuses an already configured runner" in p.stdout
    assert "token is never printed or written" in p.stdout
    assert "user-systemd" in p.stdout
    assert "aegis-ugreen-v2" in SCRIPT.read_text(encoding="utf-8")

    # First-time configuration still fails closed when no short-lived token exists.
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        fakebin = root / "bin"
        fakebin.mkdir()
        make_exe(fakebin / "gh", "#!/usr/bin/env bash\nexit 1\n")
        env = os.environ.copy()
        env.pop("AEGIS_RUNNER_TOKEN", None)
        env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
        env["AEGIS_RUNNER_DIR"] = str(root / "runner")
        env["AEGIS_RUNTIME_ROOT"] = str(root / "runtime")
        p = subprocess.run(
            ["bash", str(SCRIPT)],
            text=True,
            capture_output=True,
            env=env,
            timeout=20,
        )
        assert p.returncode == 4
        assert "No runner registration token available for first-time configuration" in p.stderr
        assert not (root / "runtime" / "Ugreen-server").exists()

    # An existing registration must be reusable without gh/token/sudo/network setup.
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        fakebin = root / "bin"
        fakebin.mkdir()
        runner = root / "runner"
        runtime = root / "runtime"
        repo = runtime / "Ugreen-server"
        runner.mkdir()
        (repo / ".git").mkdir(parents=True)
        (runner / ".runner").write_text('{"agentId":1}\n', encoding="utf-8")
        make_exe(runner / "run.sh", "#!/usr/bin/env bash\nexit 0\n")
        make_exe(fakebin / "git", "#!/usr/bin/env bash\nexit 0\n")
        make_exe(
            fakebin / "systemctl",
            "#!/usr/bin/env bash\n"
            "if [[ \"$*\" == *\"is-active\"* ]]; then exit 0; fi\n"
            "exit 0\n",
        )
        make_exe(fakebin / "gh", "#!/usr/bin/env bash\nexit 99\n")
        make_exe(fakebin / "sudo", "#!/usr/bin/env bash\nexit 99\n")

        env = os.environ.copy()
        env.pop("AEGIS_RUNNER_TOKEN", None)
        env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
        env["AEGIS_RUNNER_DIR"] = str(runner)
        env["AEGIS_RUNTIME_ROOT"] = str(runtime)
        env["HOME"] = str(root / "home")

        p = subprocess.run(
            ["bash", str(SCRIPT)],
            text=True,
            capture_output=True,
            env=env,
            timeout=20,
        )
        assert p.returncode == 0, p.stderr
        assert "runner_config=reused" in p.stdout
        assert "AEGIS_RUNNER status=healthy mode=user-systemd" in p.stdout
        state = json.loads(
            (Path(env["HOME"]) / ".local/state/aegis-runner/status.json").read_text(
                encoding="utf-8"
            )
        )
        assert state["configured"] is True
        assert state["service_mode"] == "user-systemd"
        assert state["service_active"] is True
        assert state["security"]["registration_token_persisted"] is False
        assert state["security"]["interactive_sudo_required"] is False
        assert state["security"]["opened_router_ports"] is False

    s = SCRIPT.read_text(encoding="utf-8")
    assert "[[ -s .runner && -x ./run.sh ]]" in s
    assert "gh auth status --hostname github.com" in s
    assert "actions/runners/registration-token" in s
    assert "--jq '.token'" in s
    assert "systemctl --user enable --now aegis-github-runner.service" in s
    assert "Restart=always" in s
    assert "sudo -n true" in s
    assert "sudo ./svc.sh" not in s

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
