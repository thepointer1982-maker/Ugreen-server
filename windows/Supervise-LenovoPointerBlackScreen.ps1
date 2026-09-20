param(
  [int]$Cycles = 6,
  [int]$InitialDelaySeconds = 45,
  [int]$IntervalSeconds = 60,
  [int]$FreshMinutes = 20,
  [int]$MinRepeatedSnapshots = 2,
  [int]$MinScore = 70,
  [int]$CooldownMinutes = 15,
  [string]$Root = "$env:LOCALAPPDATA\AEGIS\LenovoPointer\BlackScreen",
  [string]$CollectorPath = (Join-Path $PSScriptRoot "Collect-LenovoPointerBlackScreen.ps1"),
  [string]$ResolverPath = (Join-Path $PSScriptRoot "Resolve-LenovoPointerBlackScreen.ps1")
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Cycles = [Math]::Max(1, [Math]::Min([Math]::Abs($Cycles), 20))
$InitialDelaySeconds = [Math]::Max(0, [Math]::Min([Math]::Abs($InitialDelaySeconds), 300))
$IntervalSeconds = [Math]::Max(15, [Math]::Min([Math]::Abs($IntervalSeconds), 600))

New-Item -ItemType Directory -Path $Root -Force | Out-Null
$StateDir = Join-Path $Root "supervisor"
New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
$LockPath = Join-Path $StateDir "supervisor.lock"
$StatePath = Join-Path $StateDir "state.json"
$LogPath = Join-Path $StateDir "events.jsonl"

function Write-JsonLine {
  param([System.Collections.IDictionary]$Data)
  $copy = [ordered]@{}
  foreach ($k in $Data.Keys) { $copy[$k] = $Data[$k] }
  $copy["timestamp"] = (Get-Date).ToString("o")
  ($copy | ConvertTo-Json -Compress -Depth 12) | Add-Content -LiteralPath $LogPath -Encoding UTF8
}

function Write-State {
  param([System.Collections.IDictionary]$Data)
  $tmp = "$StatePath.tmp"
  $Data | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $tmp -Encoding UTF8
  Move-Item -LiteralPath $tmp -Destination $StatePath -Force
}

function Read-State {
  if (-not (Test-Path -LiteralPath $StatePath)) { return $null }
  try { Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json } catch { $null }
}

function Get-LatestSummary {
  if (-not (Test-Path -LiteralPath $Root)) { return $null }
  $file = Get-ChildItem -LiteralPath $Root -Filter "summary.json" -File -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch "\\(bin|package|supervisor)\\" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if (-not $file) { return $null }
  try { Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json } catch { $null }
}

function Get-ScoreValue {
  param($Summary, [string]$Name)
  if (-not $Summary -or -not $Summary.hypothesis_scores) { return 0.0 }
  $p = $Summary.hypothesis_scores.PSObject.Properties[$Name]
  if (-not $p) { return 0.0 }
  try { [double]$p.Value } catch { 0.0 }
}

function Invoke-Collector {
  if (-not (Test-Path -LiteralPath $CollectorPath)) {
    throw "Collector missing: $CollectorPath"
  }
  & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $CollectorPath -MinutesBack 15 | Out-Null
}

function Invoke-Resolver {
  param([ValidateSet("Observe","Repair")][string]$Mode)
  if (-not (Test-Path -LiteralPath $ResolverPath)) {
    throw "Resolver missing: $ResolverPath"
  }
  $raw = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $ResolverPath -Mode $Mode -FreshMinutes $FreshMinutes -MinRepeatedSnapshots $MinRepeatedSnapshots -MinScore $MinScore
  try { $raw | ConvertFrom-Json } catch {
    [pscustomobject]@{ status="resolver-output-invalid"; raw=[string]$raw }
  }
}

# Atomic single-instance lock. A stale lock is cleared only if its PID no longer exists.
$lock = $null
if (Test-Path -LiteralPath $LockPath) {
  try { $lock = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json } catch { $lock = $null }
  if ($lock -and $lock.pid) {
    $running = Get-Process -Id ([int]$lock.pid) -ErrorAction SilentlyContinue
    if ($running) {
      Write-JsonLine ([ordered]@{ status="blocked"; reason="supervisor-already-running"; pid=[int]$lock.pid })
      exit 0
    }
  }
  Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}

[ordered]@{ pid=$PID; started_at=(Get-Date).ToString("o") } |
  ConvertTo-Json | Set-Content -LiteralPath $LockPath -Encoding UTF8

try {
  if ($InitialDelaySeconds -gt 0) {
    Start-Sleep -Seconds $InitialDelaySeconds
  }

  $state = Read-State
  if ($state -and $state.cooldown_until) {
    $until = [datetime]$state.cooldown_until
    if ($until -gt (Get-Date)) {
      Write-JsonLine ([ordered]@{ status="cooldown"; cooldown_until=$until.ToString("o"); reason=$state.reason })
      exit 0
    }
  }

  $consecutiveErrors = 0
  $successfulCycles = 0

  for ($i = 1; $i -le $Cycles; $i++) {
    try {
      Invoke-Collector
      $before = Get-LatestSummary
      $observe = Invoke-Resolver -Mode "Observe"

      $event = [ordered]@{
        cycle = $i
        phase = "observe"
        resolver_status = [string]$observe.status
        hypothesis = [string]$observe.hypothesis
        score = $observe.score
        action = [string]$observe.action
      }

      # Repair mode is still fail-closed in the resolver: currently only missing Explorer
      # may be changed automatically.
      $repair = Invoke-Resolver -Mode "Repair"
      $event["repair_status"] = [string]$repair.status
      $event["repair_action"] = [string]$repair.action
      $event["repair_reason"] = [string]$repair.reason

      Invoke-Collector
      $after = Get-LatestSummary

      $beforeShell = Get-ScoreValue -Summary $before -Name "shell_dwm_explorer"
      $afterShell = Get-ScoreValue -Summary $after -Name "shell_dwm_explorer"
      $event["shell_score_before"] = $beforeShell
      $event["shell_score_after"] = $afterShell
      $event["health_delta"] = [Math]::Round(($beforeShell - $afterShell), 2)

      $explorerNow = @(
        Get-Process -Name explorer -ErrorAction SilentlyContinue |
          Where-Object { $_.SessionId -eq (Get-Process -Id $PID).SessionId }
      ).Count
      $event["explorer_processes_same_session"] = $explorerNow

      if ($repair.action -eq "safe-explorer-recovery") {
        if ($explorerNow -lt 1) {
          $event["status"] = "degraded"
          $event["reason"] = "explorer-recovery-not-confirmed"
          Write-JsonLine $event
          $cooldownUntil = (Get-Date).AddMinutes([Math]::Abs($CooldownMinutes))
          Write-State ([ordered]@{
            status="degraded"
            reason="explorer-recovery-not-confirmed"
            cooldown_until=$cooldownUntil.ToString("o")
            last_cycle=$i
          })
          break
        }
        if ($afterShell -gt $beforeShell -and $afterShell -ge $MinScore) {
          $event["status"] = "degraded"
          $event["reason"] = "post-repair-shell-score-worsened"
          Write-JsonLine $event
          $cooldownUntil = (Get-Date).AddMinutes([Math]::Abs($CooldownMinutes))
          Write-State ([ordered]@{
            status="degraded"
            reason="post-repair-shell-score-worsened"
            cooldown_until=$cooldownUntil.ToString("o")
            last_cycle=$i
          })
          break
        }
      }

      $event["status"] = "healthy-cycle"
      Write-JsonLine $event
      $successfulCycles++
      $consecutiveErrors = 0

      Write-State ([ordered]@{
        status="running"
        last_success_at=(Get-Date).ToString("o")
        last_cycle=$i
        successful_cycles=$successfulCycles
        last_hypothesis=[string]$observe.hypothesis
        last_action=[string]$repair.action
        cooldown_until=$null
      })
    } catch {
      $consecutiveErrors++
      Write-JsonLine ([ordered]@{
        cycle=$i
        status="error"
        error=$_.Exception.Message
        consecutive_errors=$consecutiveErrors
      })

      if ($consecutiveErrors -ge 2) {
        $cooldownUntil = (Get-Date).AddMinutes([Math]::Abs($CooldownMinutes))
        Write-State ([ordered]@{
          status="degraded"
          reason="repeated-supervisor-errors"
          cooldown_until=$cooldownUntil.ToString("o")
          last_cycle=$i
        })
        break
      }
    }

    if ($i -lt $Cycles) {
      Start-Sleep -Seconds $IntervalSeconds
    }
  }

  $final = Read-State
  if (-not $final) {
    $final = [pscustomobject]@{ status="completed-without-state" }
  }
  $final | ConvertTo-Json -Depth 10
}
finally {
  Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}
