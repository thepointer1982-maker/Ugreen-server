#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "aegis_zero_cost_bootstrap.sh"

def main():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "aegis/resume-pre-lenovo-20260920" in text
    assert "AEGIS_TRUSTED_SHA" in text
    assert "git clone --no-checkout" in text
    assert 'merge-base --is-ancestor "$TRUSTED_SHA"' in text
    assert 'checkout --detach --force "$TRUSTED_SHA"' in text
    assert 'git -C "$DEST" clean -fdx' in text
    assert "aegis_pull_control_install.sh" in text
    assert "aegis_codex_oss_install.sh" in text
    assert "AEGIS_ALLOW_CODEX_INSTALL" in text
    assert "AEGIS_ALLOW_MODEL_DOWNLOAD" in text
    assert "AEGIS_ALLOW_CLOUD_CODEX" in text
    assert "codex-local" in text
    assert "aegis_coder_boot_install.sh" in text
    assert "codex_oss=$CODEX_OSS_STATUS" in text
    assert "coder_boot_guardian=$CODER_BOOT_STATUS" in text
    assert "aegis_pull_control.py --repo-root" in text
    assert "aegis-pull-control/state.json" in text
    assert "gh auth status --hostname github.com" in text
    assert "aegis_access_bootstrap.sh runner" in text
    assert 'RUNNER_STATUS="skipped"' in text
    assert "runner_return_channel=$RUNNER_STATUS" in text
    assert "AEGIS_RUNNER_TOKEN" not in text
    assert "TS_AUTHKEY" not in text
    print("AEGIS ZERO COST BOOTSTRAP TESTS PASS")

if __name__ == "__main__":
    main()
