from pathlib import Path
import hashlib
import json
import tempfile
import unittest

from engine import ScoreEngine

NOW = "2026-09-21T00:00:00+02:00"


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

    def write_json(self, rel, obj):
        p = self.u / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj), encoding="utf-8")
        return p

    def write_manifest(self, paths):
        lines = []
        for p in paths:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            lines.append(f"{digest}  {p.name}")
        (self.u / "scores" / "manifest.sha256").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def verified(self, rel="scores/latest.json", score=80, metric="network_score", session="s1"):
        p = self.write_json(rel, {
            "generated_at": NOW,
            "export_session": session,
            "evidence_class": "VERIFIED_EXPORT",
            metric: score,
            "devices": [],
        })
        self.write_manifest([p])
        return p

    def test_verified_export_is_change_but_not_automatic_improvement(self):
        self.verified()
        out = self.engine().scan()
        rec = next(r for r in out["records"] if r["name"] == "network_score")
        self.assertTrue(rec["provenance_verified"])
        self.assertFalse(rec["counts_as_real_improvement"])
        self.assertEqual(out["real_verified_change_count"], 1)
        self.assertEqual(out["real_improvement_count"], 0)
        self.assertTrue(out["notification_recommended"])

    def test_fixture_never_counts_or_notifies(self):
        p = self.j / "tests" / "a1-score.json"
        p.write_text(json.dumps({
            "generated_at": NOW,
            "evidence_class": "REAL_DEVICE_MEASUREMENT",
            "device_id": "fake",
            "a1_score": 100,
        }), encoding="utf-8")
        out = self.engine().scan()
        rec = next(r for r in out["records"] if r["name"] == "a1_score")
        self.assertEqual(rec["evidence_class"], "TEST_FIXTURE")
        self.assertFalse(rec["counts_as_real_improvement"])
        self.assertFalse(out["notification_recommended"])

    def test_baseline_suppresses_unchanged(self):
        self.verified()
        engine = self.engine()
        engine.commit_baseline()
        out = engine.scan()
        self.assertEqual(out["changed_count"], 0)
        self.assertFalse(out["notification_recommended"])

    def test_hash_mismatch_alerts(self):
        self.verified()
        (self.u / "scores" / "manifest.sha256").write_text(
            "0" * 64 + "  latest.json\n", encoding="utf-8"
        )
        out = self.engine().scan()
        self.assertTrue(out["notification_recommended"])
        self.assertTrue(any(
            "HASH_MISMATCH" in (r["warning"] or "") for r in out["records"]
        ))

    def test_conflicting_verified_scores_alert(self):
        p1 = self.write_json("scores/latest.json", {
            "generated_at": NOW,
            "export_session": "s1",
            "evidence_class": "VERIFIED_EXPORT",
            "health_score": 80,
            "devices": [],
        })
        p2 = self.write_json("scores/nas-score.json", {
            "generated_at": NOW,
            "export_session": "s2",
            "evidence_class": "VERIFIED_EXPORT",
            "health_score": 55,
        })
        self.write_manifest([p1, p2])
        out = self.engine().scan()
        self.assertEqual(out["conflict_count"], 1)
        self.assertEqual(out["new_or_changed_conflict_count"], 1)
        self.assertTrue(out["notification_recommended"])

    def test_first_real_device_value_is_explicit(self):
        self.write_json("scores/device-score.json", {
            "measured_at": NOW,
            "evidence_class": "REAL_DEVICE_MEASUREMENT",
            "device_id": "ugreen-dxp4800",
            "health_score": 91,
        })
        out = self.engine().scan()
        self.assertEqual(out["first_real_device_value_count"], 1)
        rec = out["first_real_device_values"][0]
        self.assertEqual(rec["evidence_class"], "REAL_DEVICE_MEASUREMENT")
        self.assertEqual(rec["value"], 91.0)
        self.assertTrue(out["notification_recommended"])

    def test_disappeared_verified_evidence_alerts(self):
        p = self.verified()
        engine = self.engine()
        engine.commit_baseline()
        p.unlink()
        out = engine.scan()
        self.assertGreaterEqual(out["missing_verified_record_count"], 1)
        self.assertTrue(out["notification_recommended"])

    def test_provenance_change_is_change_even_same_value(self):
        p = self.verified(score=80, session="s1")
        engine = self.engine()
        engine.commit_baseline()
        p.write_text(json.dumps({
            "generated_at": NOW,
            "export_session": "s2",
            "evidence_class": "VERIFIED_EXPORT",
            "network_score": 80,
            "devices": [],
        }), encoding="utf-8")
        self.write_manifest([p])
        out = engine.scan()
        rec = next(r for r in out["records"] if r["name"] == "network_score")
        self.assertTrue(rec["changed"])
        self.assertEqual(rec["delta"], 0.0)
        self.assertEqual(rec["export_session"], "s2")
        self.assertFalse(rec["counts_as_real_improvement"])

    def test_numeric_increase_is_real_improvement_after_baseline(self):
        p = self.verified(score=80, session="s1")
        engine = self.engine()
        engine.commit_baseline()
        p.write_text(json.dumps({
            "generated_at": NOW,
            "export_session": "s2",
            "evidence_class": "VERIFIED_EXPORT",
            "network_score": 85,
            "devices": [],
        }), encoding="utf-8")
        self.write_manifest([p])
        out = engine.scan()
        rec = next(r for r in out["records"] if r["name"] == "network_score")
        self.assertEqual(rec["delta"], 5.0)
        self.assertTrue(rec["counts_as_real_improvement"])
        self.assertEqual(out["real_improvement_count"], 1)

    def test_unchanged_conflict_is_not_re_notified(self):
        p1 = self.write_json("scores/latest.json", {
            "generated_at": NOW,
            "export_session": "s1",
            "evidence_class": "VERIFIED_EXPORT",
            "health_score": 80,
            "devices": [],
        })
        p2 = self.write_json("scores/nas-score.json", {
            "generated_at": NOW,
            "export_session": "s2",
            "evidence_class": "VERIFIED_EXPORT",
            "health_score": 55,
        })
        self.write_manifest([p1, p2])
        engine = self.engine()
        first = engine.scan()
        self.assertEqual(first["new_or_changed_conflict_count"], 1)
        engine.commit_baseline()
        second = engine.scan()
        self.assertEqual(second["conflict_count"], 1)
        self.assertEqual(second["new_or_changed_conflict_count"], 0)
        self.assertFalse(second["notification_recommended"])


    def test_anomaly_score_lower_is_improvement(self):
        p = self.verified(score=40, metric="anomaly_score", session="s1")
        engine = self.engine()
        engine.commit_baseline()
        p.write_text(json.dumps({
            "generated_at": NOW,
            "export_session": "s2",
            "evidence_class": "VERIFIED_EXPORT",
            "anomaly_score": 20,
            "devices": [],
        }), encoding="utf-8")
        self.write_manifest([p])
        out = engine.scan()
        rec = next(r for r in out["records"] if r["name"] == "anomaly_score")
        self.assertEqual(rec["delta"], -20.0)
        self.assertTrue(rec["counts_as_real_improvement"])
        self.assertNotIn("NUMERIC_REGRESSION", rec["warning"] or "")

    def test_device_array_scores_do_not_false_conflict(self):
        p = self.write_json("scores/latest.json", {
            "generated_at": NOW,
            "export_session": "s1",
            "evidence_class": "VERIFIED_EXPORT",
            "network_score": 80,
            "devices": [
                {"id": "a", "score": 90},
                {"id": "b", "score": 50},
            ],
        })
        self.write_manifest([p])
        out = self.engine().scan()
        self.assertEqual(out["conflict_count"], 0)


    def test_sidecar_export_session_requires_file_hash_binding(self):
        p = self.write_json("scores/latest.json", {
            "generated_at": NOW,
            "evidence_class": "VERIFIED_EXPORT",
            "network_score": 77,
            "devices": [],
        })
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        self.write_manifest([p])
        self.write_json("scores/export-session.json", {
            "session_id": "sidecar-1",
            "artifacts": [{"path": "scores/latest.json", "sha256": digest}],
        })
        out = self.engine().scan()
        rec = next(r for r in out["records"] if r["name"] == "network_score")
        self.assertEqual(rec["export_session"], "sidecar-1")
        self.assertEqual(rec["evidence_class"], "VERIFIED_EXPORT")
        self.assertTrue(rec["provenance_verified"])

    def test_unbound_sidecar_export_session_is_not_accepted(self):
        p = self.write_json("scores/latest.json", {
            "generated_at": NOW,
            "evidence_class": "VERIFIED_EXPORT",
            "network_score": 77,
            "devices": [],
        })
        self.write_manifest([p])
        self.write_json("scores/export-session.json", {
            "session_id": "sidecar-1",
            "artifacts": [{"path": "scores/latest.json", "sha256": "0" * 64}],
        })
        out = self.engine().scan()
        rec = next(r for r in out["records"] if r["name"] == "network_score")
        self.assertIsNone(rec["export_session"])
        self.assertEqual(rec["evidence_class"], "UNKNOWN")
        self.assertFalse(rec["provenance_verified"])


if __name__ == "__main__":
    unittest.main()
