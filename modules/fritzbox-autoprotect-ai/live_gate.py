from __future__ import annotations

import argparse
import json

from block0 import Engine, load_config, public_summary


def main() -> int:
    parser = argparse.ArgumentParser(prog="aegis-autoprotect-live-gate")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    summary = public_summary(Engine(load_config(args.config)).once())
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["gate_passed"] else 20


if __name__ == "__main__":
    raise SystemExit(main())
