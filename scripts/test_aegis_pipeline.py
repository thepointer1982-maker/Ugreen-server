#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")


class AegisPipelineTests(unittest.TestCase):
    def make_repo(self, td: Path) -> Path:
        repo = td / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "scores").mkdir()
        (repo / "dashboard").mkdir()
        (repo / "fixes" / "done").mkdir(parents=True)
        for name in (
            "aegis_export_collect.sh",
            "aegis_repo_autocheck.py",
            "aegis_source_detect.sh",
            "aegis_nas_run_once.sh",
        ):
            shutil.copy2(SCRIPTS / name, repo / "scripts" / name)
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "aegis-test@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "AEGIS Test"], cwd=repo, check=True)
        (repo / ".gitkeep").write_text("\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitkeep", "scripts"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True)
        return repo

    def make_source(self, td: Path, *, deep: Optional[dict] = None, score: int = 72) -> Path:
        src = td / "source"
        latest = {
            "generated_at": "2026-08-20T12:00:00+00:00",
            "network_score": score,
            "devices": [{"id": "roku", "score": score, "reachable": True}],
            "run_id": "run-1",
        }
        write_json(src / "reports" / "latest.json", latest)
        if deep is not None:
            write_json(src / "reports" / "deepdiag.json", deep)
        return src

    def run_export(self, repo: Path, src: Path) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["AEGIS_SOURCE_ROOT"] = str(src)
        return subprocess.run(
            ["bash", str(repo / "scripts" / "aegis_export_collect.sh"), str(repo)],
            text=True,
            capture_output=True,
            env=env,
        )

    def run_once(self, repo: Path, src: Path, *args: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["AEGIS_SOURCE_ROOT"] = str(src)
        return subprocess.run(
            ["bash", str(repo / "scripts" / "aegis_nas_run_once.sh"), *args],
            cwd=repo,
            text=True,
            capture_output=True,
            env=env,
        )

    def test_stale_optional_artifacts_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw); repo = self.make_repo(td); src = self.make_source(td)
            (repo / "scores" / "deepdiag.json").write_text('{"stale":true}\n', encoding="utf-8")
            (repo / "dashboard" / "autocheck.json").write_text('{}\n', encoding="utf-8")
            (repo / "fixes" / "done" / "fix-package-002.json").write_text('{}\n', encoding="utf-8")
            cp = self.run_export(repo, src)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertFalse((repo / "scores" / "deepdiag.json").exists())
            self.assertFalse((repo / "dashboard" / "autocheck.json").exists())
            self.assertFalse((repo / "fixes" / "done" / "fix-package-002.json").exists())
            self.assertTrue((repo / "scores" / "export-session.json").exists())

    def test_mixed_deepdiag_is_rejected_without_touching_existing_export(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw); repo = self.make_repo(td)
            src = self.make_source(td, deep={"generated_at":"2026-08-20T10:00:00+00:00","deepdiag_score":80,"run_id":"different-run"})
            sentinel = repo / "scores" / "latest.json"; sentinel.write_text('{"sentinel":true}\n', encoding="utf-8")
            cp = self.run_export(repo, src)
            self.assertNotEqual(cp.returncode, 0)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), '{"sentinel":true}\n')

    def test_matching_deepdiag_is_exported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw); repo = self.make_repo(td)
            src = self.make_source(td, deep={"generated_at":"2026-08-20T12:00:30+00:00","deepdiag_score":81,"run_id":"run-1"})
            cp = self.run_export(repo, src); self.assertEqual(cp.returncode, 0, cp.stderr)
            session = json.loads((repo / "scores" / "export-session.json").read_text(encoding="utf-8"))
            self.assertEqual(session["correlation"], "shared_identifier"); self.assertIn("deepdiag.json", session["files"])

    def test_manifest_tamper_blocks_autocheck(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw); repo = self.make_repo(td); src = self.make_source(td)
            cp = self.run_export(repo, src); self.assertEqual(cp.returncode, 0, cp.stderr)
            latest = repo / "scores" / "latest.json"; latest.write_text(latest.read_text(encoding="utf-8") + " ", encoding="utf-8")
            cp = subprocess.run(["python3", str(repo / "scripts" / "aegis_repo_autocheck.py")], cwd=repo, text=True, capture_output=True)
            self.assertEqual(cp.returncode, 5); self.assertIn("manifest_hash_mismatch:latest.json", cp.stdout)

    def test_manifest_exactly_covers_session_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw); repo = self.make_repo(td); src = self.make_source(td)
            cp = self.run_export(repo, src); self.assertEqual(cp.returncode, 0, cp.stderr)
            scores = repo / "scores"; session = json.loads((scores / "export-session.json").read_text(encoding="utf-8"))
            expected = set(session["files"]) | {"export-session.json"}; names = set()
            for line in (scores / "manifest.sha256").read_text(encoding="utf-8").splitlines():
                digest, name = line.split(); self.assertEqual(hashlib.sha256((scores / name).read_bytes()).hexdigest(), digest); names.add(name)
            self.assertEqual(names, expected)

    def test_explicit_invalid_source_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            env = os.environ.copy(); env["AEGIS_SOURCE_ROOT"] = str(Path(raw) / "does-not-exist")
            cp = subprocess.run(["bash", str(SCRIPTS / "aegis_source_detect.sh")], text=True, capture_output=True, env=env)
            self.assertEqual(cp.returncode, 4); self.assertIn("invalid_explicit_source", cp.stderr)

    def test_one_shot_local_only_generates_no_commit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw); repo = self.make_repo(td); src = self.make_source(td)
            before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            cp = self.run_once(repo, src)
            after = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            self.assertEqual(cp.returncode, 0, cp.stderr); self.assertEqual(before, after)
            self.assertIn("AEGIS_RUN status=ready_local_only", cp.stdout); self.assertTrue((repo / "dashboard" / "autocheck.json").exists())

    def test_one_shot_blocks_unrelated_dirty_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw); repo = self.make_repo(td); src = self.make_source(td)
            (repo / "unrelated.txt").write_text("do not stage\n", encoding="utf-8")
            cp = self.run_once(repo, src)
            self.assertEqual(cp.returncode, 10); self.assertIn("unrelated_dirty_worktree", cp.stderr)
            self.assertFalse((repo / "scores" / "latest.json").exists())

    def test_one_shot_commit_stages_only_allowlisted_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw); repo = self.make_repo(td); src = self.make_source(td, score=35)
            cp = self.run_once(repo, src, "--commit")
            self.assertEqual(cp.returncode, 0, cp.stderr); self.assertIn("AEGIS_RUN status=committed", cp.stdout)
            changed = subprocess.check_output(["git", "show", "--name-only", "--format=", "HEAD"], cwd=repo, text=True).splitlines()
            self.assertTrue(changed)
            self.assertTrue(all(path.startswith("scores/") or path.startswith("dashboard/") or path == "fixes/done/fix-package-002.json" for path in changed))
            self.assertIn("fixes/done/fix-package-002.json", changed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
