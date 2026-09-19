import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from block0 import (
    Check,
    Config,
    Engine,
    FritzBox,
    HashAudit,
    Snapshot,
    calculate_score,
    checks_for,
    public_summary,
    rank_models,
    remote_exposure,
    secure_atomic_write,
    secure_state_dir,
    snapshot_digest,
    validate_config,
)


def by_name(checks, name):
    return next(check for check in checks if check.name == name)


def test_public_endpoint_rejected():
    with pytest.raises(ValueError):
        validate_config(Config(fritz_url="https://8.8.8.8"))


def test_local_fritz_endpoint_allowed():
    validate_config(Config(fritz_url="http://fritz.box:49000"))


def test_arbitrary_dns_hostname_rejected():
    with pytest.raises(ValueError):
        validate_config(Config(fritz_url="http://router.example:49000"))


def test_embedded_credentials_rejected():
    with pytest.raises(ValueError):
        validate_config(Config(fritz_url="http://user:pw@192.168.178.1:49000"))


def test_https_without_verification_requires_explicit_override():
    with pytest.raises(ValueError):
        validate_config(Config(fritz_url="https://192.168.178.1:49443", verify_tls=False))


def test_service_descriptor_external_url_rejected():
    fritz = FritzBox(Config(fritz_url="http://192.168.178.1:49000"))
    with pytest.raises(ValueError):
        fritz._service_url("http://example.com/evil")


def test_service_descriptor_traversal_rejected():
    fritz = FritzBox(Config(fritz_url="http://192.168.178.1:49000"))
    with pytest.raises(ValueError):
        fritz._service_url("/a/../evil")


def test_requests_does_not_trust_proxy_environment():
    fritz = FritzBox(Config(fritz_url="http://192.168.178.1:49000"))
    assert fritz.session.trust_env is False


def test_false_green_mapping_is_unknown():
    checks = checks_for(Snapshot(reachable=True, mappings_scanned=False), True)
    assert by_name(checks, "wan_port_mappings").status == "unknown"


def test_completed_empty_mapping_is_pass():
    snap = Snapshot(reachable=True, mappings_scanned=True)
    checks = checks_for(snap, True)
    assert by_name(checks, "wan_port_mappings").status == "pass"


def test_host_inventory_requires_complete_scan():
    snap = Snapshot(reachable=True, hosts=[{"NewHostName": "x"}], hosts_scanned=False)
    checks = checks_for(snap, True)
    assert by_name(checks, "device_inventory").status == "unknown"


def test_unknown_scores_zero_by_default_and_blocks_critical_gate():
    result = calculate_score([Check("critical", 100, "unknown", "", critical=True)], 70)
    assert result["score"] == 0.0
    assert result["gate_passed"] is False
    assert result["critical_blockers"] == ["critical"]


def test_critical_failure_blocks_gate_even_when_score_above_threshold():
    result = calculate_score(
        [
            Check("a", 80, "pass", ""),
            Check("b", 20, "fail", "", critical=True),
        ],
        70,
    )
    assert result["score"] == 80.0
    assert result["gate_passed"] is False


def test_remote_true_is_not_overwritten_by_later_false():
    snap = Snapshot(reachable=True, remote_scan_complete=True)
    snap.remote_probes = {
        "remote-a": {"GetInfo": {"NewEnabled": "1"}},
        "remote-b": {"GetInfo": {"NewEnabled": "0"}},
    }
    exposure = remote_exposure(snap)
    assert len(exposure) == 1
    assert next(iter(exposure.values())) == "1"


def test_active_usp_controller_is_remote_exposure():
    snap = Snapshot(reachable=True, remote_scan_complete=True)
    snap.usp_controllers = [{"NewEnable": "1", "NewHostname": "controller.example"}]
    assert remote_exposure(snap)
    assert by_name(checks_for(snap, True), "remote_management_surface").status == "fail"


def test_management_periodic_inform_is_remote_exposure():
    snap = Snapshot(reachable=True, remote_scan_complete=True)
    snap.remote_probes = {
        "urn:dslforum-org:service:ManagementServer:1": {
            "GetInfo": {"NewPeriodicInformEnable": "1", "NewUpgradesManaged": "0"}
        }
    }
    assert remote_exposure(snap)


def test_incomplete_remote_scan_is_unknown():
    snap = Snapshot(reachable=True, remote_scan_complete=False)
    snap.remote_probes = {"x": {"GetInfo": {"NewEnabled": "0"}}}
    assert by_name(checks_for(snap, True), "remote_management_surface").status == "unknown"


def test_router_prefers_coder_model():
    ranked = rank_models(["llama3:8b", "qwen2.5-coder:7b", "gemma:3b"], "code", 8)
    assert ranked[0] == "qwen2.5-coder:7b"


def test_router_avoids_70b_on_8gb():
    ranked = rank_models(["deepseek-r1:70b", "deepseek-r1:7b", "qwen:3b"], "reason", 8)
    assert ranked[0] != "deepseek-r1:70b"


def test_state_permissions_are_private():
    with TemporaryDirectory() as raw:
        state = secure_state_dir(Path(raw) / "state")
        assert stat.S_IMODE(state.stat().st_mode) == 0o700
        latest = state / "latest.json"
        secure_atomic_write(latest, "{}\n")
        assert stat.S_IMODE(latest.stat().st_mode) == 0o600


def test_symlink_state_dir_rejected():
    with TemporaryDirectory() as raw:
        base = Path(raw)
        target = base / "real"
        target.mkdir()
        link = base / "state"
        link.symlink_to(target, target_is_directory=True)
        with pytest.raises(RuntimeError):
            secure_state_dir(link)


def test_audit_tamper_returns_false_not_exception():
    with TemporaryDirectory() as raw:
        path = Path(raw) / "audit.jsonl"
        audit = HashAudit(path)
        audit.append({"x": 1})
        path.write_text("not-json\n", encoding="utf-8")
        assert audit.verify() is False


def test_hmac_audit_detects_tamper():
    with TemporaryDirectory() as raw:
        path = Path(raw) / "audit.jsonl"
        audit = HashAudit(path, b"k" * 32)
        audit.append({"x": 1})
        assert audit.integrity_mode == "hmac-sha256"
        assert audit.verify()
        text = path.read_text(encoding="utf-8").replace('"x": 1', '"x": 9')
        path.write_text(text, encoding="utf-8")
        assert audit.verify() is False


def test_public_summary_never_exposes_inventory_values():
    record = {
        "snapshot": {
            "captured_at": "2026-01-01T00:00:00+00:00",
            "reachable": True,
            "hosts": [{"NewHostName": "PRIVATE-TV", "NewIPAddress": "192.168.178.137"}],
            "hosts_scanned": True,
            "mappings": [{"NewInternalClient": "192.168.178.89", "NewExternalPort": "12345"}],
            "mappings_scanned": True,
            "remote_scan_complete": True,
            "remote_services_seen": 4,
            "usp_controllers": [{"NewHostname": "controller.example"}],
            "errors": [],
        },
        "score": {"score": 80, "gate_passed": True, "critical_blockers": []},
        "mutation_allowed": False,
        "mutation_reason": "apply_disabled",
        "audit_integrity": "sha256",
    }
    summary = public_summary(record)
    serialized = repr(summary)
    for secret in (
        "PRIVATE-TV",
        "192.168.178.137",
        "192.168.178.89",
        "12345",
        "controller.example",
    ):
        assert secret not in serialized


def test_audit_refuses_append_when_existing_chain_cannot_be_verified():
    with TemporaryDirectory() as raw:
        path = Path(raw) / "audit.jsonl"
        keyed = HashAudit(path, b"k" * 32)
        keyed.append({"x": 1})
        unkeyed = HashAudit(path)
        with pytest.raises(RuntimeError):
            unkeyed.append({"x": 2})


def test_latest_binding_detects_snapshot_tamper(tmp_path):
    cfg = Config(state_dir=str(tmp_path), audit_key_file=str(tmp_path / "missing.key"))
    engine = Engine(cfg)
    snapshot = {
        "captured_at": "2026-01-01T00:00:00+00:00",
        "reachable": True,
        "services": [],
        "device_info": {},
        "hosts": [],
        "hosts_scanned": True,
        "mappings": [],
        "mappings_scanned": True,
        "remote_probes": {},
        "remote_scan_complete": True,
        "remote_services_seen": 0,
        "usp_controllers": [],
        "errors": [],
    }
    score = {"score": 80.0, "gate_passed": True, "critical_blockers": [], "checks": []}
    record = {
        "schema_version": 2,
        "captured_at": snapshot["captured_at"],
        "config_fingerprint": engine.fingerprint,
        "snapshot": snapshot,
        "score": score,
        "audit_integrity": "sha256",
        "mutation_allowed": False,
        "mutation_reason": "apply_disabled",
    }
    engine.audit.append(
        {
            "schema_version": 2,
            "captured_at": snapshot["captured_at"],
            "config_fingerprint": engine.fingerprint,
            "snapshot_digest": snapshot_digest(snapshot),
            "score": score,
            "audit_integrity": "sha256",
            "mutation_allowed": False,
            "mutation_reason": "apply_disabled",
        }
    )
    secure_atomic_write(tmp_path / "latest.json", json.dumps(record))
    assert engine.latest() is not None
    record["snapshot"]["reachable"] = False
    secure_atomic_write(tmp_path / "latest.json", json.dumps(record))
    assert engine.latest() is None


def test_zero_remote_services_never_counts_as_complete_scan():
    fritz = FritzBox(Config(fritz_url="http://192.168.178.1:49000"))
    snap = Snapshot(reachable=True)
    fritz._probe_remote([], snap)
    assert snap.remote_services_seen == 0
    assert snap.remote_scan_complete is False
    assert "remote_services:none" in snap.errors


def test_reserved_non_lan_ip_is_rejected():
    with pytest.raises(ValueError):
        validate_config(Config(fritz_url="http://192.0.2.1:49000"))


def test_current_audited_cycle_is_not_double_counted_for_stability(tmp_path):
    key_path = tmp_path / "audit.key"
    key_path.write_bytes(b"k" * 32)
    key_path.chmod(0o600)
    cfg = Config(
        mode="apply",
        apply_enabled=True,
        min_apply_score=70,
        required_stable_cycles=3,
        state_dir=str(tmp_path / "state"),
        audit_key_file=str(key_path),
    )
    engine = Engine(cfg)
    now = datetime.now(UTC)
    captured = [
        (now - timedelta(seconds=20)).isoformat(),
        (now - timedelta(seconds=10)).isoformat(),
    ]
    for timestamp in captured:
        engine.audit.append(
            {
                "schema_version": 2,
                "captured_at": timestamp,
                "config_fingerprint": engine.fingerprint,
                "snapshot_digest": "0" * 64,
                "score": {"score": 90.0, "gate_passed": True},
                "audit_integrity": "hmac-sha256",
                "mutation_allowed": False,
                "mutation_reason": "test",
            }
        )

    allowed, reason = engine.mutation_gate(
        90.0,
        True,
        exclude_captured_at=captured[-1],
    )
    assert allowed is False
    assert reason == "insufficient_stable_cycles"

    third = now.isoformat()
    engine.audit.append(
        {
            "schema_version": 2,
            "captured_at": third,
            "config_fingerprint": engine.fingerprint,
            "snapshot_digest": "0" * 64,
            "score": {"score": 90.0, "gate_passed": True},
            "audit_integrity": "hmac-sha256",
            "mutation_allowed": False,
            "mutation_reason": "test",
        }
    )
    allowed, reason = engine.mutation_gate(90.0, True, exclude_captured_at=third)
    assert allowed is True
    assert reason == "open"
