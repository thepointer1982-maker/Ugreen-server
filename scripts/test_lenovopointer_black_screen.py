#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCIDENT = ROOT / "incidents" / "lenovopointer-windows11-black-screen.json"
COLLECT = ROOT / "windows" / "Collect-LenovoPointerBlackScreen.ps1"
WATCH = ROOT / "windows" / "Watch-LenovoPointerBlackScreen.ps1"
RECOVER = ROOT / "windows" / "Recover-LenovoPointerBlackScreen.ps1"
RESOLVE = ROOT / "windows" / "Resolve-LenovoPointerBlackScreen.ps1"
SUPERVISE = ROOT / "windows" / "Supervise-LenovoPointerBlackScreen.ps1"
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
    assert "biometric-security.json" in collect
    assert "SecureFingerprint" in collect
    assert "EssFingerprintCapable" in collect
    assert "ess_fingerprint_capable_sensor_count" in collect
    assert "Microsoft-Windows-Biometrics/Operational" in collect
    assert "Microsoft-Windows-Winlogon/Operational" in collect
    assert "1108" in collect
    assert "bioiso,ngciso" in collect
    assert "Win32_DeviceGuard" in collect
    assert "WbioSrvc" in collect
    assert "codex" in collect
    assert "usb-apple-pnp.json" in collect
    assert "audio-endpoints.json" in collect
    assert "credential-providers.json" in collect
    assert "credential-provider-filters.json" in collect
    assert "SignatureStatus" in collect
    assert "powercfg /a" in collect
    assert "echo_audio_endpoint_count" in collect
    assert "headset_audio_endpoint_count" in collect
    assert "credential_provider_filter_count" in collect
    assert "correlation-timeline.json" in collect
    assert "correlation_window_seconds" in collect
    assert "invalid_credential_provider_filter_signature_count" in collect
    assert "mere presence of credential-provider filters are informational only" in collect

    watch = WATCH.read_text(encoding="utf-8")
    assert "$delays = @(0, 30, 60, 90)" in watch
    assert "Collect-LenovoPointerBlackScreen.ps1" in watch

    recover = RECOVER.read_text(encoding="utf-8")
    assert "Start-Process -FilePath" in recover
    assert "explorer.exe" in recover
    assert "Get-Process -Name codex" in recover
    assert "Stop-Process" not in recover
    assert "Disable-PnpDevice" not in recover
    assert "Set-ItemProperty" not in recover
    assert "recovery-events.jsonl" in recover

    resolve = RESOLVE.read_text(encoding="utf-8")
    assert 'ValidateSet("Observe","Repair")' in resolve
    assert "MinRepeatedSnapshots" in resolve
    assert "MinScore" in resolve
    assert "safe-explorer-recovery" in resolve
    assert "automatic-policy-blocks-disruptive-repair" in resolve
    assert "ApplyFingerprintIsolation" not in resolve
    assert "Disable-PnpDevice" not in resolve
    assert "Set-ItemProperty" not in resolve
    assert "Stop-Process" not in resolve

    supervise = SUPERVISE.read_text(encoding="utf-8")
    assert "supervisor.lock" in supervise
    assert "CooldownMinutes" in supervise
    assert "consecutiveErrors" in supervise
    assert "post-repair-shell-score-worsened" in supervise
    assert "explorer-recovery-not-confirmed" in supervise
    assert "InitialDelaySeconds" in supervise
    assert "process_start_time" in supervise
    assert "boot_time" in supervise
    assert "invalid-cooldown-state" in supervise
    assert "supervisor-already-running" in supervise
    assert "Invoke-Resolver -Mode \"Observe\"" in supervise
    assert "Invoke-Resolver -Mode \"Repair\"" in supervise
    assert "Disable-PnpDevice" not in supervise
    assert "Set-ItemProperty" not in supervise
    assert "Stop-Process" not in supervise

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
    assert "AEGIS-LenovoPointer-BlackScreenRecovery" in install
    assert "Recover-LenovoPointerBlackScreen.ps1" in install
    assert "Resolve-LenovoPointerBlackScreen.ps1" in install
    assert "Supervise-LenovoPointerBlackScreen.ps1" in install
    assert "AEGIS-LenovoPointer-BlackScreenSupervisor" in install
    assert "-MultipleInstances IgnoreNew" in install
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
        p.read_text(encoding="utf-8") for p in [COLLECT, WATCH, RECOVER, RESOLVE, SUPERVISE, BOOTWATCH, INSTALL, REPAIR]
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
