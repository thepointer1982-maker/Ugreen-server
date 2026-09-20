#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
HEX40 = re.compile(r"^[0-9a-f]{40}$")


def main() -> None:
    workflow_files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert workflow_files, "no workflows found"

    checkout_seen = False
    for path in workflow_files:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("uses:"):
                continue
            ref = stripped.split("uses:", 1)[1].strip().split("#", 1)[0].strip()
            if "@" not in ref:
                raise AssertionError(f"{path}: unpinned action reference: {ref}")
            action, version = ref.rsplit("@", 1)
            if action.startswith("./"):
                continue
            if not HEX40.fullmatch(version):
                raise AssertionError(
                    f"{path}: action must be pinned to immutable 40-char SHA: {ref}"
                )
            if action == "actions/checkout":
                checkout_seen = True

    assert checkout_seen, "pinned actions/checkout not found"

    autocheck = (WORKFLOWS / "aegis-autocheck.yml").read_text(encoding="utf-8")
    assert '"mcp[cli]==2.2.0"' in autocheck
    assert '"mcp[cli]>=2,<3"' not in autocheck
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in autocheck

    runner = (ROOT / "scripts" / "aegis_runner_install.sh").read_text(encoding="utf-8")
    assert "sha256sum -c -" in runner
    assert "70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613" in runner
    assert "9b1dc70626422526e3c94767cf024896beb15da5342a3f4819bf2feac13e0393" in runner

    opencode = (ROOT / "scripts" / "aegis_opencode_install.sh").read_text(encoding="utf-8")
    assert "sha256sum -c -" in opencode
    assert "AEGIS_ALLOW_OPENCODE_INSTALL:-0" in opencode

    print("AEGIS SUPPLY CHAIN TESTS PASS")


if __name__ == "__main__":
    main()
