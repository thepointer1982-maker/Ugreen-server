#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCIDENT = ROOT / "incidents" / "lenovopointer-windows11-black-screen.json"
COLLECT = ROOT / "windows" / "Collect-LenovoPointerBlackScreen.ps1"
WATCH = ROOT / "windows" / "Watch-LenovoPointerBlackScreen.ps1"
BOOTWATCH = ROOT / "windows" / "Watch-LenovoPointerBlackScreenBoot.ps1"
INSTALL = ROOT / "windows" / "Install-LenovoPointerBlackScreenWatch.ps1"
REPAIR = ROOT / "windows" / "Repair-LenovoPointerBlackScreen.ps1"
DOC = ROOT / "docs" / "lenovopointer-black-screen.md"

DANGEROUS = [
    "Clear-Disk",
    "Initialize-Disk",
    "Remove-Partition",
    "Format-Volume",
    "Set-Partition",
    "diskpart",
    "bcdedit",
    "reagentc",
    "pnputil /delete-driver",
    "Remove-PnpDevice",
]

def main() -> None:
    incident = json.loads(INCIDENT.read_text(encoding="utf-8"))
    assert incident["id"] == "lenovopointer-windows11-black-screen"
    assert incident["status"] in {"evidence-required", "instrumented-awaiting-real-failure-evidence"}
    assert incident["observed"]["causal_link_to_echo_studio"] == "not-proven"
    assert incident["cost"] == "free-built-in-Windows-tools-only"
    if incident["status"] == "instrumented-awaiting-real-failure-evidence":
        assert incident["validation"]["conclusion"] == "success"
        assert incident["implementation"]["collector"] == "windows/Collect-LenovoPointerBlackScreen.ps1"
    assert "automatic display-driver uninstall" in incident["safety"]["forbidden"]

    collect = COLLECT.read_text(encoding="utf-8")
    assert "Win32_VideoController" in collect
    assert "Get-PnpDevice -Class Display" in collect
    assert "LiveKernelReports" in collect
    assert "HiberbootEnabled" in collect
    assert "4101" in collect
    assert "summary.json" in collect
    assert "Get-NetConnectionProfile" in collect
    assert "Get-PnpDevice -Class Biometric" in collect
    assert "Microsoft-Windows-Biometrics/Operational" in collect
    assert "Microsoft-Windows-Winlogon/Operational" in collect
    assert "1108" in collect
    assert "bioiso,ngciso" in collect
    assert "Win32_DeviceGuard" in collect
    assert "WbioSrvc" in collect
    assert "codex" in collect
    assert "usb-apple-pnp.json" in collect
    assert "powercfg /a" in collect

    watch = WATCH.read_text(encoding="utf-8")
    assert "$delays = @(0, 30, 60, 90)" in watch
    assert "Collect-LenovoPointerBlackScreen.ps1" in watch

    bootwatch = BOOTWATCH.read_text(encoding="utf-8")
    assert "$offsets = @(0, 10, 20, 30, 45, 60, 90, 120, 180)" in bootwatch
    assert "$env:ProgramData" in bootwatch
    assert "-OutputRoot $OutputRoot" in bootwatch

    install = INSTALL.read_text(encoding="utf-8")
    assert "New-ScheduledTaskTrigger -AtLogOn" in install
    assert "New-ScheduledTaskTrigger -AtStartup" in install
    assert '-UserId "SYSTEM"' in install
    assert "-LogonType ServiceAccount" in install
    assert "-RunLevel Highest" in install
    assert "icacls.exe" in install
    assert "InstallBoot" in install and "InstallAll" in install
    assert "Unregister-ScheduledTask" in install

    repair = REPAIR.read_text(encoding="utf-8")
    for action in [
        "PlanEchoIsolation",
        "ApplyEchoIsolation",
        "RestoreEcho",
        "PlanEchoAudioIsolation",
        "ApplyEchoAudioIsolation",
        "RestoreEchoAudio",
        "PlanFingerprintIsolation",
        "ApplyFingerprintIsolation",
        "RestoreFingerprint",
        "RestartBiometricService",
        "DisableFastStartup",
        "RestoreFastStartup",
        "RestartExplorer",
    ]:
        assert action in repair
    assert "already-applied" in repair
    assert "echo-audio-isolation.json" in repair
    assert '@("AudioEndpoint","Media","Bluetooth")' in repair
    assert "already-disabled" in repair
    assert "ConfirmAlternativeSignIn" in repair
    assert "fingerprint-isolation.json" in repair
    assert "Wi-Fi, biometric devices, and Codex were not touched" in repair
    assert "Disable-PnpDevice" in repair
    assert "Enable-PnpDevice" in repair
    assert "HiberbootEnabled" in repair

    combined = "\n".join(
        p.read_text(encoding="utf-8") for p in [COLLECT, WATCH, BOOTWATCH, INSTALL, REPAIR]
    )
    for token in DANGEROUS:
        assert token.lower() not in combined.lower(), token
    assert "Stop-Process -Name codex" not in combined
    assert "taskkill /im codex" not in combined.lower()

    doc = DOC.read_text(encoding="utf-8")
    assert "Win+Ctrl+Shift+B" in doc
    assert "Echo Studio" in doc
    assert "does **not** prove" in doc
    assert "No Wi-Fi passwords" in doc

    print("AEGIS LENOVOPOINTER BLACK-SCREEN TESTS PASS")

if __name__ == "__main__":
    main()
