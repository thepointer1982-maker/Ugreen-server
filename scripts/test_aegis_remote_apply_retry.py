#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "aegis_remote_apply_retry.sh"

def main() -> None:
    help_run = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        text=True,
        capture_output=True,
    )
    assert help_run.returncode == 0
    assert "No passwords are read or stored" in help_run.stdout

    bad = subprocess.run(
        ["bash", str(SCRIPT), "--attempts", "0"],
        text=True,
        capture_output=True,
    )
    assert bad.returncode == 2
    assert "Invalid attempt count" in bad.stderr

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        repo = root / "repo"
        scripts = repo / "scripts"
        scripts.mkdir(parents=True)
        status = root / "status.json"
        attempts = root / "attempts.txt"

        deploy_lines = [
            "#!/usr/bin/env bash",
            "n=$(cat '" + str(attempts) + "' 2>/dev/null || echo 0)",
            "n=$((n+1))",
            "echo $n > '" + str(attempts) + "'",
            "if [[ $n -ge 2 ]]; then",
            "  echo '{\"health\":\"healthy\",\"blockers\":[]}' > '" + str(status) + "'",
            "else",
            "  echo '{\"health\":\"degraded\",\"blockers\":[]}' > '" + str(status) + "'",
            "fi",
            "exit 0",
            "",
        ]
        (scripts / "aegis_deploy_local.sh").write_text("\n".join(deploy_lines), encoding="utf-8")
        (scripts / "aegis_real_cycle.py").write_text("print('fake')\n", encoding="utf-8")
        (scripts / "aegis_real_status.py").write_text("print('fake-status')\n", encoding="utf-8")

        env = os.environ.copy()
        env["AEGIS_REAL_STATUS_FILE"] = str(status)
        env["AEGIS_REMOTE_BASE_DELAY"] = "0"
        env["AEGIS_REMOTE_MAX_DELAY"] = "0"
        run = subprocess.run(
            ["bash", str(SCRIPT), "--repo-root", str(repo), "--attempts", "3"],
            text=True,
            capture_output=True,
            env=env,
        )
        assert run.returncode == 0, run.stderr + run.stdout
        assert attempts.read_text().strip() == "2"

    print("AEGIS REMOTE RETRY TESTS PASS")

if __name__ == "__main__":
    main()
