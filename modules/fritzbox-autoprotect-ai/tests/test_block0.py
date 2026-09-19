from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from block0 import Check, Config, HashAudit, Snapshot, checks_for, rank_models, calculate_score, validate_config


def by_name(checks, name):
    return next(check for check in checks if check.name == name)


def test_public_endpoint_rejected():
    with pytest.raises(ValueError):
        validate_config(Config(fritz_url="https://8.8.8.8"))


def test_local_fritz_endpoint_allowed():
    validate_config(Config(fritz_url="http://fritz.box:49000"))


def test_false_green_mapping_is_unknown():
    checks = checks_for(Snapshot(reachable=True, mappings_scanned=False), True)
    assert by_name(checks, "wan_port_mappings").status == "unknown"


def test_completed_empty_mapping_is_pass():
    checks = checks_for(Snapshot(reachable=True, mappings_scanned=True), True)
    assert by_name(checks, "wan_port_mappings").status == "pass"


def test_host_inventory_requires_complete_scan():
    snap = Snapshot(reachable=True, hosts=[{"NewHostName": "x"}], hosts_scanned=False)
    checks = checks_for(snap, True)
    assert by_name(checks, "device_inventory").status == "unknown"


def test_weighted_score_is_conservative():
    result = calculate_score([
        Check("a", 50, "pass", ""),
        Check("b", 30, "fail", ""),
        Check("c", 20, "unknown", ""),
    ], 70)
    assert result["score"] == 57.0
    assert result["gate_passed"] is False


def test_router_prefers_coder_model():
    ranked = rank_models(["llama3:8b", "qwen2.5-coder:7b", "gemma:3b"], "code", 8)
    assert ranked[0] == "qwen2.5-coder:7b"


def test_router_avoids_70b_on_8gb():
    ranked = rank_models(["deepseek-r1:70b", "deepseek-r1:7b", "qwen:3b"], "reason", 8)
    assert ranked[0] != "deepseek-r1:70b"


def test_audit_tamper_detected():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "audit.jsonl"
        audit = HashAudit(path)
        audit.append({"x": 1})
        audit.append({"y": 2})
        assert audit.verify()
        path.write_text(path.read_text().replace('"x": 1', '"x": 9', 1), encoding="utf-8")
        assert not audit.verify()


def test_apply_is_closed_by_default(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    engine_allowed = cfg.mode == "apply" and cfg.apply_enabled
    assert engine_allowed is False
