#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "scripts" / "aegis_coder_boot_guard.sh"
INSTALL = ROOT / "scripts" / "aegis_coder_boot_install.sh"


def make_exe(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def main() -> None:
    guard = GUARD.read_text(encoding="utf-8")
    install = INSTALL.read_text(encoding="utf-8")

    assert "codex-local" in guard
    assert "AEGIS_ALLOW_CLOUD_CODEX" in guard
    assert "unset OPENAI_API_KEY" in guard
    assert "CODEX_ACCESS_TOKEN" in guard
    assert '"runs_model_task_at_boot": False' in guard
    assert '"cloud_model_usage_default": False' in guard
    assert "codex exec" not in guard
    assert "opencode run" not in guard
    assert "OnBootSec=45s" in install
    assert "OnUnitActiveSec=10min" in install
    assert "Persistent=true" in install
    assert "NoNewPrivileges=true" in install
    assert "ProtectHome=read-only" in install
    assert "codex-local" in install

    with tempfile.TemporaryDirectory() as raw:
        temp = Path(raw)
        fakebin = temp / "bin"
        fakebin.mkdir()
        state = temp / "state"
        oss_state = temp / "oss.json"
        wrapper = temp / "aegis-codex-local"
        wrapper.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)
        oss_state.write_text(
            json.dumps(
                {
                    "health": "healthy",
                    "provider": "ollama",
                    "model_ready": True,
                    "cloud_model_usage": False,
                    "openai_api_key_required": False,
                }
            ),
            encoding="utf-8",
        )

        make_exe(
            fakebin / "codex",
            '#!/usr/bin/env bash\n'
            'if [[ "$1 $2" == "login status" ]]; then echo "Logged in using ChatGPT"; exit 0; fi\n'
            'exit 99\n',
        )
        make_exe(fakebin / "opencode", "#!/usr/bin/env bash\nexit 0\n")
        make_exe(fakebin / "curl", "#!/usr/bin/env bash\nexit 0\n")

        env = os.environ.copy()
        env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
        env["AEGIS_CODER_REPO_ROOT"] = str(ROOT)
        env["AEGIS_CODER_BOOT_STATE_DIR"] = str(state)
        env["AEGIS_CODEX_OSS_STATE_FILE"] = str(oss_state)
        env["AEGIS_CODEX_OSS_WRAPPER"] = str(wrapper)
        env["AEGIS_CODER_PREFER"] = "codex-local"
        env["AEGIS_ALLOW_CLOUD_CODEX"] = "0"
        env["OPENAI_API_KEY"] = "must-not-be-used"
        env["CODEX_API_KEY"] = "must-not-be-used"
        env["CODEX_ACCESS_TOKEN"] = "must-not-be-used"

        cp = subprocess.run(
            ["bash", str(GUARD)],
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
        )
        assert cp.returncode == 0, cp.stderr
        data = json.loads((state / "status.json").read_text(encoding="utf-8"))
        assert data["schema"] == "aegis-coder-boot/v2"
        assert data["health"] == "healthy"
        assert data["selected"] == "codex-local"
        assert data["codex_local"]["ready"] is True
        assert data["codex_local"]["provider"] == "ollama"
        assert data["codex_local"]["cloud_model_usage"] is False
        assert data["codex_cloud"]["ready"] is True
        assert data["codex_cloud"]["automatic_fallback_allowed"] is False
        assert data["boot_policy"]["runs_model_task_at_boot"] is False
        assert data["boot_policy"]["external_api_key_required"] is False
        assert data["boot_policy"]["cloud_model_usage_default"] is False

    print("AEGIS CODER BOOT TESTS PASS")


if __name__ == "__main__":
    main()
