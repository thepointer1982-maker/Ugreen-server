#!/usr/bin/env python3
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "aegis_coder_router.sh"
CONFIG = ROOT / "config" / "opencode" / "aegis-local.json"

def main():
    s = SCRIPT.read_text(encoding="utf-8")
    assert "unset OPENAI_API_KEY" in s
    assert "codex-local" in s
    assert "AEGIS_CODEX_OSS_WRAPPER" in s
    assert "AEGIS_CODEX_OSS_STATE_FILE" in s
    assert "AEGIS_ALLOW_CLOUD_CODEX" in s
    assert "AEGIS_PROJECT_CONTEXT_REQUIRED" in s
    assert "aegis_project_context.py" in s
    assert "AEGIS AUTHORITATIVE PROJECT CONTEXT" in s
    assert "project-context.json" in s
    assert "Continue the existing project rather than starting a new project." in s
    assert "cloud Codex fallback is disabled" in s
    assert "CODEX_API_KEY CODEX_ACCESS_TOKEN" in s
    assert 'grep -Fq "Logged in using ChatGPT"' in s
    assert "codex exec --ignore-user-config --ephemeral --sandbox workspace-write" in s
    assert '"$LOCAL_CODEX_WRAPPER" exec "$FULL_TASK"' in s
    assert 'opencode run --dir "$WORKTREE" --model "ollama/$LOCAL_MODEL" --agent build "$FULL_TASK"' in s
    assert "OPENCODE_DISABLE_AUTOUPDATE=1" in s
    assert "OPENCODE_DISABLE_MODELS_FETCH=1" in s
    assert "OPENCODE_DISABLE_DEFAULT_PLUGINS=1" in s
    assert "OPENCODE_DISABLE_LSP_DOWNLOAD=1" in s
    assert "OPENCODE_AUTO_SHARE=false" in s
    assert "AEGIS_CODER_BOOT_STATE_FILE" in s
    assert "aegis_local_model_select.py" in s
    assert "aegis_opencode_guard.py" in s
    assert 'local_model=$LOCAL_MODEL' in s
    assert "deepseek-r1:8b" in json.dumps(c)
    assert "boot_selected" in s
    assert "timeout 8s codex login status" in s
    assert "git -C \"$REPO_ROOT\" worktree add --detach" in s
    assert "git -C \"$WORKTREE\" diff --check" in s
    c = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert c["model"] == "ollama/qwen2.5-coder:7b"
    assert c["provider"]["ollama"]["options"]["baseURL"] == "http://127.0.0.1:11434/v1"
    assert c["permission"]["external_directory"] == "deny"
    assert c["permission"]["webfetch"] == "deny"
    assert c["permission"]["websearch"] == "deny"
    assert c["permission"]["bash"]["*"] == "deny"
    print("AEGIS CODER ROUTER TESTS PASS")

if __name__ == "__main__":
    main()
