param(
  [ValidateSet("Observe","Repair")]
  [string]$Mode = "Observe",
  [int]$FreshMinutes = 20,
  [int]$MinRepeatedSnapshots = 2,
  [int]$MinScore = 70,
  [string]$UserRoot = "$env:LOCALAPPDATA\AEGIS\LenovoPointer\BlackScreen",
  [string]$BootRoot = "$env:ProgramData\AEGIS\LenovoPointer\BlackScreen",
  [string]$RecoveryPath = (Join-Path $PSScriptRoot "Recover-LenovoPointerBlackScreen.ps1")
)

$ErrorActionPreference = "Stop"

function Read-Summary {
  param([System.IO.FileInfo]$File, [string]$Source)
  try {
    $d = Get-Content -LiteralPath $File.FullName -Raw | ConvertFrom-Json
    if (-not $d.collected_at -or -not $d.hypothesis_scores) { return $null }
    $t = [datetime]$d.collected_at
    $age = ((Get-Date) - $t).TotalMinutes
    if ($age -lt 0 -or $age -gt [Math]::Abs($FreshMinutes)) { return $null }
    [pscustomobject]@{
      path = $File.FullName
      source = $Source
      collected_at = $t
      age_minutes = [Math]::Round($age, 2)
      data = $d
    }
  } catch { $null }
}

function Get-RecentSummaries {
  $items = @()
  foreach ($pair in @(
    [pscustomobject]@{ root=$UserRoot; source="user" },
    [pscustomobject]@{ root=$BootRoot; source="boot" }
  )) {
    if (-not (Test-Path -LiteralPath $pair.root)) { continue }
    Get-ChildItem -LiteralPath $pair.root -Filter "summary.json" -File -Recurse -ErrorAction SilentlyContinue |
      Where-Object { $_.FullName -notmatch "\\(bin|package)\\" } |
      ForEach-Object {
        $x = Read-Summary -File $_ -Source $pair.source
        if ($x) { $items += $x }
      }
  }
  @($items | Sort-Object collected_at)
}

function Get-Score {
  param($Summary, [string]$Name)
  $p = $Summary.data.hypothesis_scores.PSObject.Properties[$Name]
  if (-not $p) { return 0.0 }
  try { [double]$p.Value } catch { 0.0 }
}

function Decision {
  param(
    [string]$Status,
    [string]$Hypothesis,
    [double]$Score,
    [int]$Repeats,
    [string]$Action,
    [string]$Reason,
    $Evidence
  )
  [ordered]@{
    schema = "aegis-lenovopointer-resolver/v1"
    decided_at = (Get-Date).ToString("o")
    mode = $Mode
    status = $Status
    hypothesis = $Hypothesis
    score = [Math]::Round($Score, 2)
    repeated_snapshots = $Repeats
    action = $Action
    reason = $Reason
    evidence = $Evidence
    safety = "Fail-closed: only missing Explorer may be auto-started. No driver, Windows Hello enrollment, fingerprint device, USB, Bluetooth, boot, firmware, partition, or Codex process is changed automatically."
  }
}

$summaries = @(Get-RecentSummaries)
if ($summaries.Count -eq 0) {
  Decision -Status "blocked" -Hypothesis "unknown" -Score 0 -Repeats 0 -Action "none" -Reason "no-fresh-summary-evidence" -Evidence @() |
    ConvertTo-Json -Depth 10
  exit 10
}

$names = @(
  "windows_hello_biometrics",
  "gpu_display_driver",
  "shell_dwm_explorer",
  "bluetooth_wifi_combo",
  "usb_c_apple_path",
  "fast_startup_candidate"
)

$agg = @()
foreach ($name in $names) {
  $scores = @($summaries | ForEach-Object { Get-Score -Summary $_ -Name $name })
  $max = 0.0
  if ($scores.Count -gt 0) { $max = [double](($scores | Measure-Object -Maximum).Maximum) }
  $repeats = @($scores | Where-Object { $_ -ge $MinScore }).Count
  $agg += [pscustomobject]@{ name=$name; max=$max; repeats=$repeats }
}

$winner = $agg |
  Sort-Object @{Expression="repeats";Descending=$true}, @{Expression="max";Descending=$true} |
  Select-Object -First 1

$latest = $summaries[-1]
$envelope = [ordered]@{
  summary_count = $summaries.Count
  latest_path = $latest.path
  latest_source = $latest.source
  latest_collected_at = $latest.collected_at.ToString("o")
  aggregate = $agg
}

if (-not $winner -or $winner.max -lt $MinScore -or $winner.repeats -lt $MinRepeatedSnapshots) {
  Decision -Status "observe" -Hypothesis $(if ($winner) { $winner.name } else { "unknown" }) -Score $(if ($winner) { $winner.max } else { 0 }) -Repeats $(if ($winner) { $winner.repeats } else { 0 }) -Action "collect-more" -Reason "repeat-and-score-gate-not-met" -Evidence $envelope |
    ConvertTo-Json -Depth 10
  exit 0
}

$hyp = [string]$winner.name
$score = [double]$winner.max
$repeats = [int]$winner.repeats

$planned = switch ($hyp) {
  "shell_dwm_explorer" { "safe-explorer-recovery" }
  "windows_hello_biometrics" { "manual-gated-fingerprint-or-biometric-service-a-b-test" }
  "gpu_display_driver" { "graphics-reset-or-supported-driver-gate" }
  "bluetooth_wifi_combo" { "manual-gated-echo-audio-a-b-test" }
  "usb_c_apple_path" { "manual-usb-c-a-b-test" }
  "fast_startup_candidate" { "manual-gated-fast-startup-a-b-test" }
  default { "none" }
}

if ($Mode -eq "Observe" -or $hyp -ne "shell_dwm_explorer") {
  Decision -Status "ready" -Hypothesis $hyp -Score $score -Repeats $repeats -Action $planned -Reason $(if ($Mode -eq "Observe") { "observe-only" } else { "automatic-policy-blocks-disruptive-repair" }) -Evidence $envelope |
    ConvertTo-Json -Depth 10
  exit 0
}

if (-not (Test-Path -LiteralPath $RecoveryPath)) {
  Decision -Status "blocked" -Hypothesis $hyp -Score $score -Repeats $repeats -Action "none" -Reason "recovery-script-missing" -Evidence $envelope |
    ConvertTo-Json -Depth 10
  exit 11
}

$raw = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $RecoveryPath -DelaySeconds 0 -OutputRoot $UserRoot
$repair = try { $raw | ConvertFrom-Json } catch { $raw }
$d = Decision -Status "repair-attempted" -Hypothesis $hyp -Score $score -Repeats $repeats -Action "safe-explorer-recovery" -Reason "repeated-shell-evidence" -Evidence $envelope
$d["repair_result"] = $repair
$d | ConvertTo-Json -Depth 12
