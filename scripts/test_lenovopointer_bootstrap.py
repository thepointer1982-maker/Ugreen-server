#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "windows" / "Bootstrap-LenovoPointerBlackScreen.ps1"

def main():
    s = SCRIPT.read_text(encoding="utf-8")
    assert 'ValidatePattern("^[0-9a-f]{40}$")' in s
    assert "raw.githubusercontent.com/thepointer1982-maker/Ugreen-server/$TrustedSha/windows" in s
    assert "Install-LenovoPointerBlackScreenWatch.ps1" in s
    assert "Collect-LenovoPointerBlackScreen.ps1" in s
    assert "Repair-LenovoPointerBlackScreen.ps1" in s
    assert "Invoke-WebRequest" in s
    assert "forbidden token" in s
    assert "-Action InstallAll" in s or "-Action InstallBoot" in s
    assert "Start-Process powershell.exe -Verb RunAs" in s
    assert "-MinutesBack 30" in s
    assert "AEGIS_RUNNER_TOKEN" not in s
    assert "TS_AUTHKEY" not in s
    print("AEGIS LENOVOPOINTER BOOTSTRAP TESTS PASS")

if __name__ == "__main__":
    main()
