#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "aegis_mcp_runtime_install.sh"


def main() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "mcp[cli]>=2,<3" in text
    assert "python3 -m venv" in text
    assert "network_listener" in text
    assert '"public_port": False' in text
    assert "sudo" not in text
    assert "exec \"$MCP_PYTHON\" \"$REPO_ROOT/scripts/aegis_mcp_server.py\"" in text

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        fakepkg = root / "fakepkg"
        (fakepkg / "mcp" / "server").mkdir(parents=True)
        (fakepkg / "mcp" / "__init__.py").write_text("", encoding="utf-8")
        (fakepkg / "mcp" / "server" / "__init__.py").write_text(
            "class MCPServer:\n    pass\n", encoding="utf-8"
        )
        home = root / "home"
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["PYTHONPATH"] = str(fakepkg)
        env["AEGIS_REPO_ROOT"] = str(ROOT)
        env["AEGIS_MCP_RUNTIME_ROOT"] = str(root / "runtime")
        env["AEGIS_MCP_STATE_DIR"] = str(root / "state")
        env["AEGIS_LOCAL_BIN_DIR"] = str(root / "bin")
        env["AEGIS_ALLOW_MCP_INSTALL"] = "0"

        cp = subprocess.run(
            ["bash", str(SCRIPT)],
            text=True,
            capture_output=True,
            env=env,
            timeout=20,
        )
        assert cp.returncode == 0, cp.stderr
        state = json.loads((root / "state" / "runtime.json").read_text(encoding="utf-8"))
        assert state["health"] == "healthy"
        assert state["transport"] == "stdio"
        assert state["network_listener"] is False
        assert state["public_port"] is False
        assert state["install_performed"] is False
        wrapper = (root / "bin" / "aegis-mcp-local").read_text(encoding="utf-8")
        assert "AEGIS_REPO_ROOT" in wrapper
        assert "AEGIS_PROJECT_STATE_DIR" in wrapper
        assert "aegis_mcp_server.py" in wrapper

    print("AEGIS MCP RUNTIME TESTS PASS")


if __name__ == "__main__":
    main()
