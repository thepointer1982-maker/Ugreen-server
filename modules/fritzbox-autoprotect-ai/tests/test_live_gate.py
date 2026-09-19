from block0 import public_summary


def test_public_summary_is_count_only_for_private_network_inventory():
    record = {
        "snapshot": {
            "captured_at": "2026-01-01T00:00:00+00:00",
            "reachable": True,
            "hosts": [{"NewHostName": "PRIVATE-TV", "NewIPAddress": "192.168.178.137"}],
            "hosts_scanned": True,
            "mappings": [{"NewInternalClient": "192.168.178.89", "NewExternalPort": "12345"}],
            "mappings_scanned": True,
            "remote_scan_complete": True,
            "remote_services_seen": 3,
            "usp_controllers": [],
            "errors": [],
        },
        "score": {"score": 91.0, "gate_passed": True, "critical_blockers": []},
        "mutation_allowed": False,
        "mutation_reason": "apply_disabled",
        "audit_integrity": "hmac-sha256",
    }
    summary = public_summary(record)
    serialized = repr(summary)
    assert "PRIVATE-TV" not in serialized
    assert "192.168.178.137" not in serialized
    assert "192.168.178.89" not in serialized
    assert "12345" not in serialized
    assert summary["hosts_count"] == 1
    assert summary["port_mappings_count"] == 1
