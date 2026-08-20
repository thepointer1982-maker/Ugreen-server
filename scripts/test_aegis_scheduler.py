#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "aegis_scheduled_run.sh"
INSTALLER = ROOT / "scripts" / "aegis_scheduler_install.sh"


class SchedulerTests(unittest.TestCase):
    def make_repo(self, td: Path, exit_code: int) -> Path:
        repo = td / "repo"
        scripts = repo / "scripts"
        scripts.mkdir(parents=True)
        run_once = scripts / "aegis_nas_run_once.sh"
        run_once.write_text(
            "#!/usr/bin/env bash\necho fake-run >> \"${AEGIS_TEST_COUNTER}\"\nexit %d\n" % exit_code,
            encoding="utf-8",
        )
        return repo

    def run_wrapper(self, repo: Path, state: Path, counter: Path):
        env = os.environ.copy()
        env.update({
            "AEGIS_REPO_ROOT": str(repo),
            "AEGIS_SCHEDULER_STATE_DIR": str(state),
            "AEGIS_TEST_COUNTER": str(counter),
            "AEGIS_SCHEDULER_PUSH": "0",
        })
        return subprocess.run(["bash", str(WRAPPER)], text=True, capture_output=True, env=env)

    def run_installer(self, *args: str):
        return subprocess.run(
            ["bash", str(INSTALLER), *args],
            text=True,
            capture_output=True,
        )

    def test_failure_creates_backoff_and_second_run_skips(self):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            repo = self.make_repo(td, 7)
            state, counter = td / "state", td / "counter"
            first = self.run_wrapper(repo, state, counter)
            self.assertEqual(first.returncode, 7)
            self.assertIn("failures=1", (state / "state.env").read_text())
            second = self.run_wrapper(repo, state, counter)
            self.assertEqual(second.returncode, 0)
            self.assertIn("reason=backoff", second.stdout)
            self.assertEqual(counter.read_text().count("fake-run"), 1)

    def test_success_resets_failure_state(self):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            repo = self.make_repo(td, 0)
            state, counter = td / "state", td / "counter"
            result = self.run_wrapper(repo, state, counter)
            self.assertEqual(result.returncode, 0)
            text = (state / "state.env").read_text()
            self.assertIn("failures=0", text)
            self.assertIn("next_allowed=0", text)

    def test_installer_system_dry_run_defaults_to_local_only(self):
        result = self.run_installer("--mode", "system", "--run-user", "aegis-test", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("status=dry_run mode=system", result.stdout)
        self.assertIn("Environment=AEGIS_SCHEDULER_PUSH=0", result.stdout)
        self.assertIn("User=aegis-test", result.stdout)
        self.assertIn("OnUnitActiveSec=15min", result.stdout)

    def test_installer_user_dry_run_defaults_to_local_only(self):
        result = self.run_installer("--mode", "user", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("status=dry_run mode=user", result.stdout)
        self.assertIn("Environment=AEGIS_SCHEDULER_PUSH=0", result.stdout)
        self.assertNotIn("\nUser=", result.stdout)

    def test_installer_push_requires_explicit_flag(self):
        result = self.run_installer("--mode", "system", "--run-user", "aegis-test", "--push", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("push=1", result.stdout)
        self.assertIn("Environment=AEGIS_SCHEDULER_PUSH=1", result.stdout)

    def test_installer_refuses_too_frequent_schedule(self):
        result = self.run_installer("--interval-minutes", "4", "--dry-run")
        self.assertEqual(result.returncode, 64)
        self.assertIn("interval below 5 minutes refused", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
