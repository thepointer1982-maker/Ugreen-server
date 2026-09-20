# LenovoPointer – Windows 11 black screen after login

## Incident

Observed: shortly after Windows 11 starts/logs in, Echo Studio connects over Bluetooth or Wi-Fi and the screen becomes black.

The timing is recorded as evidence. It does **not** prove that Echo Studio causes the black screen.

## Primary hypotheses

1. GPU/display-driver TDR or reset.
2. DWM / Explorer shell failure.
3. Display output/topology switch.
4. Bluetooth/Wi-Fi combo-driver conflict around the Echo connection.
5. Fast Startup / hybrid boot state.

## Immediate no-cost recovery when the screen turns black

Try in this order and note which one restores the image:

1. `Win+Ctrl+Shift+B` — reset the Windows graphics-driver path.
2. `Win+P`, then choose the internal display / PC screen only.
3. `Ctrl+Alt+Del`; if that screen appears, open Task Manager and restart Windows Explorer.

These steps are also diagnostic signals. If the first shortcut restores the image, GPU/display moves to the top of the evidence list.

## Collect evidence

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\windows\Collect-LenovoPointerBlackScreen.ps1 -MinutesBack 30
```

Local output only:

```text
%LOCALAPPDATA%\AEGIS\LenovoPointer\BlackScreen\<timestamp>
```

The collector records GPU driver/version, display devices, Echo/Bluetooth/WLAN PnP endpoints, network adapter state, DWM/Explorer state, relevant Windows events, LiveKernelReport metadata, Fast Startup state, and a rule-based `summary.json`.

No Wi-Fi passwords, browser data, credentials, or user documents are collected.

## Install automatic post-logon capture

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\windows\Install-LenovoPointerBlackScreenWatch.ps1 -Action Install
```

The current-user task runs after logon and collects several evidence snapshots during the first minutes.

Status / removal:

```powershell
.\windows\Install-LenovoPointerBlackScreenWatch.ps1 -Action Status
.\windows\Install-LenovoPointerBlackScreenWatch.ps1 -Action Remove
```

## Reversible repair gates

First inspect:

```powershell
.\windows\Repair-LenovoPointerBlackScreen.ps1 -Action Status
.\windows\Repair-LenovoPointerBlackScreen.ps1 -Action PlanEchoIsolation
```

### Echo Studio A/B isolation

Use only if the collected evidence points at Bluetooth/WLAN/PnP around the failure:

```powershell
# elevated PowerShell
.\windows\Repair-LenovoPointerBlackScreen.ps1 -Action ApplyEchoIsolation
```

Test one boot/login. Then restore:

```powershell
.\windows\Repair-LenovoPointerBlackScreen.ps1 -Action RestoreEcho
```

Only matching Echo-like PnP endpoints are touched; the Wi-Fi adapter and Bluetooth radio are not blanket-disabled.

### Fast Startup A/B test

Use only if **Restart works but Shut down -> power on reproduces the failure**:

```powershell
# elevated PowerShell
.\windows\Repair-LenovoPointerBlackScreen.ps1 -Action DisableFastStartup
```

Restore:

```powershell
.\windows\Repair-LenovoPointerBlackScreen.ps1 -Action RestoreFastStartup
```

### Explorer-only recovery

```powershell
.\windows\Repair-LenovoPointerBlackScreen.ps1 -Action RestartExplorer
```

## Explicit safety boundary

AEGIS does not automatically format disks, alter partitions, edit EFI/bootloaders, modify BIOS, delete display drivers, or install a replacement GPU driver in this incident path.

If evidence identifies the display driver, the next gate is to record the exact adapter/driver version and then use Windows Update / Lenovo-supported rollback or update with a restore path.


## New priority: fingerprint / power-button / Windows Hello transition

The failure is now reported immediately before sign-in while using the thumb/fingerprint button. On Lenovo systems where the fingerprint reader is integrated with the power button, one physical interaction can participate in both wake/power and Windows Hello authentication. The collector therefore treats this as the highest-priority path until evidence rules it out.

AEGIS now records:

- biometric PnP endpoints and signed biometric driver metadata
- Windows Biometric Service (`WbioSrvc`)
- `Microsoft-Windows-Biometrics/Operational`
- biometric Event ID 1108
- `bioiso.exe` / `ngciso.exe`
- Device Guard / VBS state
- Winlogon / LogonUI / userinit / DWM / Explorer process state
- pre-login snapshots from a SYSTEM startup task
- Codex process liveness without collecting its prompt or command line
- Apple/iPhone USB-C PnP presence and USB/PnP warning/error events

### Pre-login watcher

The normal user watcher starts only after logon, which can miss this failure. The new SYSTEM watcher starts at Windows startup and samples the first three minutes:

```powershell
# elevated PowerShell
.\windows\Install-LenovoPointerBlackScreenWatch.ps1 -Action InstallBoot
```

Or install both user + boot watchers:

```powershell
# elevated PowerShell
.\windows\Install-LenovoPointerBlackScreenWatch.ps1 -Action InstallAll
```

Boot evidence is written to:

```text
C:\ProgramData\AEGIS\LenovoPointer\BlackScreen
```

The SYSTEM watcher directory is ACL-restricted; authenticated users receive read/execute only.

### Fingerprint A/B isolation

First inspect only:

```powershell
.\windows\Repair-LenovoPointerBlackScreen.ps1 -Action PlanFingerprintIsolation
```

For one controlled boot, only if PIN/password sign-in is known to work:

```powershell
# elevated PowerShell
.\windows\Repair-LenovoPointerBlackScreen.ps1 -Action ApplyFingerprintIsolation -ConfirmAlternativeSignIn
```

Then sign in using PIN/password instead of the fingerprint reader. Restore afterwards:

```powershell
# elevated PowerShell
.\windows\Repair-LenovoPointerBlackScreen.ps1 -Action RestoreFingerprint
```

This does not delete Windows Hello enrollment. It disables only the matching biometric PnP endpoint for the A/B test and preserves a restore record.

### Codex preservation

Codex is treated as a protected workload. AEGIS records only process liveness/CPU/memory metadata for `codex`, `node`, `python`, `pwsh`, and related processes. The incident scripts do not terminate Codex and do not collect prompt text or command-line arguments.

### USB-C / iPhone

The current iPhone USB-C connection is recorded as a separate correlation path. A connected iPhone is not assumed to cause the black screen. The collector records Apple/iPhone PnP presence plus USBHUB3/USBXHCI/Kernel-PnP warnings/errors so its timing can be compared with the sign-in failure.


### Narrow Echo Studio audio/Bluetooth isolation

For the current live test, keep the wired headset connected and isolate only Echo Studio endpoints that Windows classifies as `AudioEndpoint`, `Media`, or `Bluetooth`:

```powershell
# inspect only
.\windows\Repair-LenovoPointerBlackScreen.ps1 -Action PlanEchoAudioIsolation

# elevated PowerShell: isolate Echo Studio only
.\windows\Repair-LenovoPointerBlackScreen.ps1 -Action ApplyEchoAudioIsolation
```

This deliberately does **not** disable the Wi-Fi adapter, Bluetooth radio, biometric reader, Windows Hello, or Codex.

Restore after the A/B test:

```powershell
# elevated PowerShell
.\windows\Repair-LenovoPointerBlackScreen.ps1 -Action RestoreEchoAudio
```

Interpretation:

- wired headset + Echo isolated + no black screen: Echo/Bluetooth/audio PnP transition becomes a strong candidate.
- black screen still occurs: Echo timing was likely incidental; fingerprint/Hello, GPU/DWM, or Winlogon stay higher.
