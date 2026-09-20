from pathlib import Path
import hashlib
import json
import tempfile
import unittest

from engine import ScoreEngine


class ScoreEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.u = self.root / "Ugreen"
        self.j = self.root / "OpenJarvis"
        self.d = self.root / "Drive"
        self.state = self.root / "state" / "baseline.json"
        (self.u / "scores").mkdir(parents=True)
        (self.j / "tests").mkdir(parents=True)
        (self.d / "02_Index").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def engine(self):
        return ScoreEngine(
            {"ugreen-server": self.u, "openjarvis-main": self.j, "drive-datahub": self.d},
            self.state,
            max_age_hours=24,
        )

    def write_verified_export(self, score=80):
        p = self.u / "scores" / "latest.json"
        p.write_text(json.dumps({
            "generated_at": "2026-09-21T00:00:00+02:00",
            "export_session": "session-1",
            "evidence_class": "VERIFIED_EXPORT",
            "network_score": score,
            "devices": [],
        }), encoding="utf-8")
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        (p.parent / "manifest.sha256").write_text(f"{digest}  latest.json\n", encoding="utf-8")

    def test_verified_export_is_countable(self):
        self.write_verified_export()
        out = self.engine().scan()
        rec = next(r for r in out["records"] if r["name"] == "network_score")
        self.assertEqual(rec["evidence_class"], "VERIFIED_EXPORT")
        self.assertTrue(rec["hash_verified"])
        self.assertTrue(rec["counts_as_real_improvement"])

    def test_fixture_never_counts_or_notifies(self):
        p = self.j / "tests" / "a1-score.json"
        p.write_text(json.dumps({
            "generated_at": "2026-09-21T00:00:00+02:00",
            "evidence_class": "REAL_DEVICE_MEASUREMENT",
            "device_id": "fake",
            "a1_score": 100,
        }), encoding="utf-8")
        out = self.engine().scan()
        rec = next(r for r in out["records"] if r["name"] == "a1_score")
        self.assertEqual(rec["evidence_class"], "TEST_FIXTURE")
        self.assertFalse(rec["counts_as_real_improvement"])
        self.assertFalse(out["notification_recommended"])

    def test_baseline_suppresses_unchanged_values(self):
        self.write_verified_export()
        self.engine().commit_baseline()
        out = self.engine().scan()
        self.assertEqual(out["changed_count"], 0)
        self.assertFalse(out["notification_recommended"])

    def test_hash_mismatch_is_integrity_alert(self):
        self.write_verified_export()
        manifest = self.u / "scores" / "manifest.sha256"
        manifest.write_text("0" * 64 + "  latest.json\n", encoding="utf-8")
        out = self.engine().scan()
        rec = next(r for r in out["records"] if r["name"] == "network_score")
        self.assertFalse(rec["hash_verified"])
        self.assertIn("HASH_MISMATCH", rec["warning"])
        self.assertTrue(out["notification_recommended"])


if __name__ == "__main__":
    unittest.main()
