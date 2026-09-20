param(
  [ValidateSet("Install","InstallBoot","InstallAll","Remove","RemoveBoot","RemoveAll","Status")]
  [string]$Action = "InstallAll"
)

$ErrorActionPreference = "Stop"
$UserTaskName = "AEGIS-LenovoPointer-BlackScreenWatch"
$RecoveryTaskName = "AEGIS-LenovoPointer-BlackScreenRecovery"
$BootTaskName = "AEGIS-LenovoPointer-BlackScreenBootWatch"
$UserRoot = Join-Path $env:LOCALAPPDATA "AEGIS\LenovoPointer\BlackScreen"
$UserBin = Join-Path $UserRoot "bin"
$BootRoot = Join-Path $env:ProgramData "AEGIS\LenovoPointer\BlackScreen"
$BootBin = Join-Path $BootRoot "bin"

$CollectorSrc = Join-Path $PSScriptRoot "Collect-LenovoPointerBlackScreen.ps1"
$WatchSrc = Join-Path $PSScriptRoot "Watch-LenovoPointerBlackScreen.ps1"
$RecoverySrc = Join-Path $PSScriptRoot "Recover-LenovoPointerBlackScreen.ps1"
$BootWatchSrc = Join-Path $PSScriptRoot "Watch-LenovoPointerBlackScreenBoot.ps1"
$CollectorDst = Join-Path $UserBin "Collect-LenovoPointerBlackScreen.ps1"
$WatchDst = Join-Path $UserBin "Watch-LenovoPointerBlackScreen.ps1"
$RecoveryDst = Join-Path $UserBin "Recover-LenovoPointerBlackScreen.ps1"
$BootCollectorDst = Join-Path $BootBin "Collect-LenovoPointerBlackScreen.ps1"
$BootWatchDst = Join-Path $BootBin "Watch-LenovoPointerBlackScreenBoot.ps1"

function Test-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $p = New-Object Security.Principal.WindowsPrincipal($id)
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Require-Admin {
  if (-not (Test-Admin)) { throw "Boot watcher installation requires an elevated PowerShell session." }
}

function Get-TaskState {
  param([string]$Name)
  $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
  if (-not $task) { return [ordered]@{ installed=$false } }
  $info = Get-ScheduledTaskInfo -TaskName $Name -ErrorAction SilentlyContinue
  return [ordered]@{
    installed = $true
    state = [string]$task.State
    last_run_time = $info.LastRunTime
    last_task_result = $info.LastTaskResult
    next_run_time = $info.NextRunTime
  }
}

function Show-Status {
  [pscustomobject]@{
    admin = (Test-Admin)
    user_task = Get-TaskState $UserTaskName
    recovery_task = Get-TaskState $RecoveryTaskName
    boot_task = Get-TaskState $BootTaskName
    user_evidence_root = $UserRoot
    boot_evidence_root = $BootRoot
  } | ConvertTo-Json -Depth 6
}

function Install-UserWatch {
  foreach ($path in @($CollectorSrc,$WatchSrc,$RecoverySrc)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Required file missing: $path" }
  }
  New-Item -ItemType Directory -Path $UserBin -Force | Out-Null
  Copy-Item -LiteralPath $CollectorSrc -Destination $CollectorDst -Force
  Copy-Item -LiteralPath $WatchSrc -Destination $WatchDst -Force
  Copy-Item -LiteralPath $RecoverySrc -Destination $RecoveryDst -Force

  $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
  $quoted = "`"$WatchDst`""
  $taskAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File $quoted"
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
  $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName $UserTaskName -Action $taskAction -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null

  $recoveryArgs = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$RecoveryDst`" -DelaySeconds 20 -CollectorPath `"$CollectorDst`" -OutputRoot `"$UserRoot`""
  $recoveryAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $recoveryArgs
  Register-ScheduledTask -TaskName $RecoveryTaskName -Action $recoveryAction -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
}

function Protect-BootRoot {
  if (-not (Get-Command icacls.exe -ErrorAction SilentlyContinue)) { throw "icacls.exe is required to protect the SYSTEM watcher directory." }
  & icacls.exe $BootRoot /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-11:(OI)(CI)RX" /T /C | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Failed to harden boot watcher ACLs." }
}

function Install-BootWatch {
  Require-Admin
  foreach ($path in @($CollectorSrc,$BootWatchSrc)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Required file missing: $path" }
  }
  New-Item -ItemType Directory -Path $BootBin -Force | Out-Null
  Copy-Item -LiteralPath $CollectorSrc -Destination $BootCollectorDst -Force
  Copy-Item -LiteralPath $BootWatchSrc -Destination $BootWatchDst -Force
  Protect-BootRoot

  $args = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$BootWatchDst`" -CollectorPath `"$BootCollectorDst`" -OutputRoot `"$BootRoot`""
  $taskAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $args
  $trigger = New-ScheduledTaskTrigger -AtStartup
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
  $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
  Register-ScheduledTask -TaskName $BootTaskName -Action $taskAction -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
}

function Remove-UserWatch {
  Unregister-ScheduledTask -TaskName $UserTaskName -Confirm:$false -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $RecoveryTaskName -Confirm:$false -ErrorAction SilentlyContinue
}

function Remove-BootWatch {
  Require-Admin
  Unregister-ScheduledTask -TaskName $BootTaskName -Confirm:$false -ErrorAction SilentlyContinue
}

switch ($Action) {
  "Status" { Show-Status; exit 0 }
  "Install" { Install-UserWatch; Show-Status; exit 0 }
  "InstallBoot" { Install-BootWatch; Show-Status; exit 0 }
  "InstallAll" {
    Install-UserWatch
    if (Test-Admin) {
      Install-BootWatch
    } else {
      Write-Warning "Pre-login boot watcher was not installed because this shell is not elevated."
    }
    Show-Status
    exit 0
  }
  "Remove" { Remove-UserWatch; Show-Status; exit 0 }
  "RemoveBoot" { Remove-BootWatch; Show-Status; exit 0 }
  "RemoveAll" {
    Remove-UserWatch
    if (Test-Admin) { Remove-BootWatch } else { Write-Warning "Boot task removal requires elevation." }
    Show-Status
    exit 0
  }
}
