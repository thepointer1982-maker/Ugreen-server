param(
  [int]$DelaySeconds = 20,
  [string]$CollectorPath = (Join-Path $PSScriptRoot "Collect-LenovoPointerBlackScreen.ps1"),
  [string]$OutputRoot = "$env:LOCALAPPDATA\AEGIS\LenovoPointer\BlackScreen"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if ($DelaySeconds -gt 0) {
  Start-Sleep -Seconds ([Math]::Min([Math]::Abs($DelaySeconds), 120))
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
$logPath = Join-Path $OutputRoot "recovery-events.jsonl"
$now = Get-Date
$currentSession = (Get-Process -Id $PID).SessionId

function Write-RecoveryEvent {
  param([System.Collections.IDictionary]$Event)
  $copy = [ordered]@{}
  foreach ($key in $Event.Keys) { $copy[$key] = $Event[$key] }
  $copy["timestamp"] = (Get-Date).ToString("o")
  $copy["session_id"] = $currentSession
  ($copy | ConvertTo-Json -Compress -Depth 8) | Add-Content -LiteralPath $logPath -Encoding UTF8
}

# Evidence is collected before any recovery action. Collector failures never trigger
# broader repair; they are only recorded.
$collectorStatus = "not-run"
if (Test-Path -LiteralPath $CollectorPath) {
  try {
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $CollectorPath -MinutesBack 10 | Out-Null
    $collectorStatus = "ok"
  } catch {
    $collectorStatus = "failed: $($_.Exception.Message)"
  }
}

$codexCount = @(Get-Process -Name codex -ErrorAction SilentlyContinue).Count
$explorerBefore = @(
  Get-Process -Name explorer -ErrorAction SilentlyContinue |
    Where-Object { $_.SessionId -eq $currentSession }
)

# Fail closed if Winlogon is configured to use a nonstandard shell. We do not
# overwrite registry values automatically.
$shell = $null
try {
  $shell = [string](Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -Name Shell -ErrorAction Stop).Shell
} catch {
  $shell = $null
}

if ($shell -and $shell -notmatch "(?i)^explorer\.exe$") {
  $result = [ordered]@{
    status = "blocked-nonstandard-shell"
    configured_shell = $shell
    collector = $collectorStatus
    explorer_before = $explorerBefore.Count
    codex_processes_alive = $codexCount
    action = "none"
    safety = "No process was terminated and no registry, driver, biometric, boot, USB, Bluetooth, or network setting was changed."
  }
  Write-RecoveryEvent -Event $result
  $result | ConvertTo-Json -Depth 6
  exit 2
}

if ($explorerBefore.Count -gt 0) {
  $result = [ordered]@{
    status = "healthy-shell-present"
    collector = $collectorStatus
    explorer_before = $explorerBefore.Count
    explorer_after = $explorerBefore.Count
    codex_processes_alive = $codexCount
    action = "none"
    safety = "Codex and all existing processes were preserved."
  }
  Write-RecoveryEvent -Event $result
  $result | ConvertTo-Json -Depth 6
  exit 0
}

# The only automatic repair: start the standard Windows shell in the already
# interactive user session. No existing process is stopped.
try {
  Start-Process -FilePath "$env:WINDIR\explorer.exe" -ErrorAction Stop
  Start-Sleep -Seconds 5
} catch {
  $result = [ordered]@{
    status = "explorer-start-failed"
    collector = $collectorStatus
    explorer_before = 0
    explorer_after = 0
    codex_processes_alive = $codexCount
    action = "start-explorer"
    error = $_.Exception.Message
  }
  Write-RecoveryEvent -Event $result
  $result | ConvertTo-Json -Depth 6
  exit 3
}

$explorerAfter = @(
  Get-Process -Name explorer -ErrorAction SilentlyContinue |
    Where-Object { $_.SessionId -eq $currentSession }
)

$result = [ordered]@{
  status = $(if ($explorerAfter.Count -gt 0) { "recovered-explorer" } else { "explorer-not-confirmed" })
  collector = $collectorStatus
  explorer_before = 0
  explorer_after = $explorerAfter.Count
  codex_processes_alive = $codexCount
  action = "start-explorer"
  changed_at = (Get-Date).ToString("o")
  safety = "Only explorer.exe was started. Nothing was terminated; Codex, DWM, GPU drivers, Windows Hello, fingerprint, USB, Bluetooth, network, boot and firmware were untouched."
}
Write-RecoveryEvent -Event $result
$result | ConvertTo-Json -Depth 6
if ($explorerAfter.Count -gt 0) { exit 0 } else { exit 4 }
