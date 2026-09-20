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
