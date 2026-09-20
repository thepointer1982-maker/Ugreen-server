#!/usr/bin/env python3
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "aegis_bootstrap_from_github.sh"

def main():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "aegis/resume-pre-lenovo-20260920" in text
    assert "git clone --branch" in text
    assert "git -C \"$DEST\" clean -fdx" in text
    assert "aegis_access_bootstrap.sh preflight" in text
    assert "aegis_access_bootstrap.sh runner" in text
    assert "AEGIS_RUNNER_TOKEN" not in text
    print("AEGIS GITHUB BOOTSTRAP TESTS PASS")

if __name__ == "__main__":
    main()
