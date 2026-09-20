#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "scripts" / "aegis_codex_oss_install.sh"
PROFILE = ROOT / "config" / "codex" / "aegis-local.config.toml"


def make_exe(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def main() -> None:
    install_text = INSTALL.read_text(encoding="utf-8")
    profile_text = PROFILE.read_text(encoding="utf-8")

    assert "https://chatgpt.com/codex/install.sh" in install_text
    assert "CODEX_NON_INTERACTIVE=1" in install_text
    assert "AEGIS_ALLOW_CODEX_INSTALL" in install_text
    assert "AEGIS_ALLOW_MODEL_DOWNLOAD" in install_text
    assert "unset OPENAI_API_KEY" in install_text
    assert "CODEX_ACCESS_TOKEN" in install_text
    assert "--oss --local-provider ollama" in install_text
    assert "--ask-for-approval never" in install_text
    assert 'web_search="disabled"' in install_text
    assert "cloud_model_usage" in install_text
    assert "chatgpt_auth_required" in install_text

    assert 'oss_provider = "ollama"' in profile_text
    assert 'web_search = "disabled"' in profile_text
    assert 'approval_policy = "never"' in profile_text
    assert 'sandbox_mode = "workspace-write"' in profile_text
    assert "network_access = false" in profile_text

    with tempfile.TemporaryDirectory() as raw:
        temp = Path(raw)
        fakebin = temp / "fakebin"
        outbin = temp / "bin"
        home = temp / "codex-home"
        state = temp / "state"
        mcp_wrapper = temp / "aegis-mcp-local"
        fakebin.mkdir()
        outbin.mkdir()
        mcp_wrapper.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        mcp_wrapper.chmod(0o755)

        make_exe(
            fakebin / "codex",
            '#!/usr/bin/env bash\n'
            'if [[ "${1:-}" == "--help" ]]; then echo "--oss --local-provider"; exit 0; fi\n'
            'exit 0\n',
        )
        make_exe(fakebin / "curl", "#!/usr/bin/env bash\nexit 0\n")
        make_exe(
            fakebin / "ollama",
            '#!/usr/bin/env bash\n'
            'if [[ "${1:-}" == "pull" ]]; then exit 0; fi\n'
            'exit 0\n',
        )

        env = os.environ.copy()
        env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
        env["AEGIS_CODER_REPO_ROOT"] = str(ROOT)
        env["AEGIS_LOCAL_BIN_DIR"] = str(outbin)
        env["AEGIS_CODEX_OSS_HOME"] = str(home)
        env["AEGIS_CODEX_OSS_STATE_DIR"] = str(state)
        env["AEGIS_MCP_WRAPPER"] = str(mcp_wrapper)
        env["AEGIS_ALLOW_MODEL_DOWNLOAD"] = "1"
        env["AEGIS_ALLOW_CODEX_INSTALL"] = "0"
        env["OPENAI_API_KEY"] = "must-never-be-forwarded"
        env["CODEX_API_KEY"] = "must-never-be-forwarded"
        env["CODEX_ACCESS_TOKEN"] = "must-never-be-forwarded"

        cp = subprocess.run(
            ["bash", str(INSTALL)],
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
        )
        assert cp.returncode == 0, cp.stderr

        generated_profile = (home / "aegis-local.config.toml").read_text(encoding="utf-8")
        assert "[mcp_servers.aegis_local]" in generated_profile
        assert f'command = "{mcp_wrapper}"' in generated_profile
        assert f'cwd = "{ROOT}"' in generated_profile
        assert "required = true" in generated_profile
        assert "startup_timeout_sec = 3" in generated_profile
        assert "tool_timeout_sec = 10" in generated_profile
        assert '"project_context"' in generated_profile
        assert '"record_project_handoff"' in generated_profile
        assert '"primary_control_status"' in generated_profile
        assert '"service_lifecycle_status"' in generated_profile
        assert '"autonomy_status"' in generated_profile
        assert '"autonomy_policy"' in generated_profile
        assert '"enqueue_autonomy_task"' in generated_profile
        assert '"run_autonomy_cycle"' in generated_profile
        assert "AEGIS_AUTONOMY_POLICY" in generated_profile
        assert "AEGIS_AUTONOMY_STATE_DIR" in generated_profile

        wrapper = (outbin / "aegis-codex-local").read_text(encoding="utf-8")
        assert f'export CODEX_HOME="{home}"' in wrapper
        assert "unset OPENAI_API_KEY" in wrapper
        assert "CODEX_API_KEY CODEX_ACCESS_TOKEN" in wrapper
        assert 'if [[ "${1:-}" == "exec" ]]' in wrapper
        assert 'MODEL="${AEGIS_LOCAL_CODER_MODEL:-qwen2.5-coder:7b}"' in wrapper
        assert "codex exec --oss --local-provider ollama" in wrapper
        assert "--ask-for-approval never" in wrapper
        assert "-c 'web_search=\"disabled\"'" in wrapper

        data = json.loads((state / "status.json").read_text(encoding="utf-8"))
        assert data["health"] == "healthy"
        assert data["provider"] == "ollama"
        assert data["model"] == "qwen2.5-coder:7b"
        assert data["cloud_model_usage"] is False
        assert data["chatgpt_auth_required"] is False
        assert data["openai_api_key_required"] is False
        assert data["web_search"] == "disabled"
        assert data["shell_network_access"] is False
        assert data["mcp_attached"] is True
        assert Path(data["mcp_wrapper"]) == mcp_wrapper
        assert Path(data["isolated_codex_home"]) == home

    print("AEGIS CODEX OSS TESTS PASS")


if __name__ == "__main__":
    main()
