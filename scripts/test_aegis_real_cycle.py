#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT = SCRIPT_DIR / "aegis_real_cycle.py"
spec = importlib.util.spec_from_file_location("aegis_real_cycle", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    report = {
        "inventory": {
            "sqlite_databases": [
                {"path": "/old-trace.db", "mtime": 10, "trace_metrics": {"count": 3}},
                {"path": "/new-trace.db", "mtime": 20, "trace_metrics": {"count": 4}},
                {"path": "/telemetry.db", "mtime": 15, "telemetry_metrics": {"count": 5}},
                {"path": "/other.db", "mtime": 99},
            ]
        }
    }
    selected = mod.select_database_sources(report)
    assert selected["trace_db"] == "/new-trace.db"
    assert selected["telemetry_db"] == "/telemetry.db"
    assert mod.select_database_sources({}) == {
        "trace_db": None,
        "telemetry_db": None,
    }

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        repo = root / "repo"
        scripts = repo / "scripts"
        scripts.mkdir(parents=True)

        write(
            scripts / "aegis_nas_bootstrap.sh",
            """#!/usr/bin/env bash
set -e
mkdir -p "$AEGIS_STATE_DIR"
python3 - "$AEGIS_STATE_DIR/preflight.json" "$2" "$AEGIS_TEST_SOURCE_ROOT" <<'PY'
import json, sys
from pathlib import Path
out, repo, source = sys.argv[1:4]
Path(out).write_text(json.dumps({
    "status": "ready",
    "repo_root": str(Path(repo).resolve()),
    "blockers": [],
    "source": {"status": "ready", "root": source},
    "git": {"is_repo": True},
}), encoding="utf-8")
PY
exit 0
""",
        )
        write(
            scripts / "aegis_nas_run_once.sh",
            """#!/usr/bin/env bash
set -e
printf '%s' "${AEGIS_SOURCE_ROOT:-}" > "$AEGIS_TEST_SOURCE_CAPTURE"
exit 0
""",
        )
        write(
            scripts / "aegis_local_ai_miner.py",
            """import hashlib, json, os
from pathlib import Path
state=Path(os.environ["AEGIS_AI_MINER_STATE_DIR"])
state.mkdir(parents=True, exist_ok=True)
payload={"schema":"fake-miner","inventory":{"sqlite_databases":[]}}
canonical=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
digest=hashlib.sha256(canonical).hexdigest()
payload["_provenance"]={"algorithm":"sha256","kind":"local-ai-miner","sha256":digest,"parent_sha256":None,"parent_kind":None}
(state/"latest.json").write_text(json.dumps(payload), encoding="utf-8")
""",
        )
        write(
            scripts / "aegis_real_status.py",
            """import json, os, sys
from pathlib import Path
out=Path(os.environ.get("AEGIS_REAL_STATUS_FILE", Path.home()/".local/state/aegis-real-status/latest.json"))
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({"health":"healthy"}), encoding="utf-8")
raise SystemExit(0)
""",
        )
        write(
            scripts / "aegis_guardian_cycle.py",
            """import json, os
from pathlib import Path
state=Path(os.environ["AEGIS_GUARDIAN_STATE_DIR"])
state.mkdir(parents=True, exist_ok=True)
Path(os.environ["AEGIS_TEST_GUARDIAN_CAPTURE"]).write_text(
    os.environ.get("AEGIS_GUARDIAN_REUSE_PREFLIGHT", ""),
    encoding="utf-8",
)
(state/"status.json").write_text(json.dumps({"mode":"healthy","reason":"verified-cycle"}), encoding="utf-8")
""",
        )

        env = os.environ.copy()
        source_root = root / "source"
        source_root.mkdir()
        source_capture = root / "source-capture"
        guardian_capture = root / "guardian-capture"
        env["AEGIS_REAL_CYCLE_STATE_DIR"] = str(root / "real")
        env["AEGIS_AI_MINER_STATE_DIR"] = str(root / "ai")
        env["AEGIS_GUARDIAN_STATE_DIR"] = str(root / "guardian")
        env["AEGIS_STATE_DIR"] = str(root / "bootstrap-state")
        env["AEGIS_TEST_SOURCE_ROOT"] = str(source_root)
        env["AEGIS_TEST_SOURCE_CAPTURE"] = str(source_capture)
        env["AEGIS_TEST_GUARDIAN_CAPTURE"] = str(guardian_capture)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(repo)],
            text=True,
            capture_output=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        summary = json.loads((root / "real" / "latest.json").read_text())
        assert summary["status"] == "healthy"
        assert summary["matrix"]["status"] == "skipped"
        assert summary["miner_provenance"]["verified"] is True
        assert summary["steps"]["guardian"]["rc"] == 0
        assert summary["steps"]["real_status"]["rc"] == 0
        assert summary["source_reuse"]["used_for_export"] is True
        assert summary["source_reuse"]["root"] == str(source_root)
        assert source_capture.read_text(encoding="utf-8") == str(source_root)
        assert guardian_capture.read_text(encoding="utf-8") == "1"
        assert summary["_provenance"]["kind"] == "real-cycle"

    print("AEGIS REAL CYCLE TESTS PASS")


if __name__ == "__main__":
    main()
