from __future__ import annotations

import argparse
import json

from block0 import Engine, load_config


def redact(record: dict) -> dict:
    snapshot = record.get("snapshot", {})
    score = record.get("score", {})
    return {
        "score": score.get("score"),
        "gate_passed": score.get("gate_passed"),
        "mutation_allowed": record.get("mutation_allowed", False),
        "mutation_reason": record.get("mutation_reason", "unknown"),
        "fritzbox_reachable": bool(snapshot.get("reachable", False)),
        "hosts_count": len(snapshot.get("hosts", [])),
        "hosts_scan_complete": bool(snapshot.get("hosts_scanned", False)),
        "port_mappings_count": len(snapshot.get("mappings", [])),
        "port_mappings_scan_complete": bool(snapshot.get("mappings_scanned", False)),
        "errors_count": len(snapshot.get("errors", [])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="aegis-autoprotect-live-gate")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    result = Engine(load_config(args.config)).once()
    summary = redact(result)
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["gate_passed"] else 20


if __name__ == "__main__":
    raise SystemExit(main())
