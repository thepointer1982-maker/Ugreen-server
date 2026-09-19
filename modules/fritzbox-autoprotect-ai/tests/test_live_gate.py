from live_gate import redact


def test_redaction_never_exposes_device_details():
    record = {
        "snapshot": {
            "reachable": True,
            "hosts": [{"NewHostName": "PRIVATE-TV", "NewIPAddress": "192.168.178.137"}],
            "hosts_scanned": True,
            "mappings": [{"NewInternalClient": "192.168.178.89", "NewExternalPort": "12345"}],
            "mappings_scanned": True,
            "errors": [],
            "device_info": {"NewSerialNumber": "PRIVATE"},
            "remote_probes": {"secret": "PRIVATE"},
        },
        "score": {"score": 91.0, "gate_passed": True},
        "mutation_allowed": False,
        "mutation_reason": "apply_disabled",
    }
    summary = redact(record)
    serialized = repr(summary)
    assert "PRIVATE-TV" not in serialized
    assert "192.168.178.137" not in serialized
    assert "192.168.178.89" not in serialized
    assert "PRIVATE" not in serialized
    assert summary["hosts_count"] == 1
    assert summary["port_mappings_count"] == 1
