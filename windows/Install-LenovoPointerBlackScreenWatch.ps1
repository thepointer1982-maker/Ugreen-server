param(
  [ValidateSet("Install","Remove","Status")]
  [string]$Action = "Install"
)

$ErrorActionPreference = "Stop"
$TaskName = "AEGIS-LenovoPointer-BlackScreenWatch"
$Root = Join-Path $env:LOCALAPPDATA "AEGIS\LenovoPointer\BlackScreen"
$Bin = Join-Path $Root "bin"
$CollectorSrc = Join-Path $PSScriptRoot "Collect-LenovoPointerBlackScreen.ps1"
$WatchSrc = Join-Path $PSScriptRoot "Watch-LenovoPointerBlackScreen.ps1"
$CollectorDst = Join-Path $Bin "Collect-LenovoPointerBlackScreen.ps1"
$WatchDst = Join-Path $Bin "Watch-LenovoPointerBlackScreen.ps1"

function Show-Status {
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if (-not $task) {
    [pscustomobject]@{ TaskName=$TaskName; Installed=$false; Root=$Root } | ConvertTo-Json
    return
  }
  $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
  [pscustomobject]@{
    TaskName=$TaskName
    Installed=$true
    State=$task.State
    LastRunTime=$info.LastRunTime
    LastTaskResult=$info.LastTaskResult
    NextRunTime=$info.NextRunTime
    Root=$Root
  } | ConvertTo-Json -Depth 4
}

if ($Action -eq "Status") { Show-Status; exit 0 }

if ($Action -eq "Remove") {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  Show-Status
  exit 0
}

foreach ($path in @($CollectorSrc,$WatchSrc)) {
  if (-not (Test-Path -LiteralPath $path)) { throw "Required file missing: $path" }
}
New-Item -ItemType Directory -Path $Bin -Force | Out-Null
Copy-Item -LiteralPath $CollectorSrc -Destination $CollectorDst -Force
Copy-Item -LiteralPath $WatchSrc -Destination $WatchDst -Force

$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$quoted = "`"$WatchDst`""
$taskAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File $quoted"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Show-Status
