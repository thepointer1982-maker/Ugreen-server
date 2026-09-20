param(
  [string]$CollectorPath = (Join-Path $PSScriptRoot "Collect-LenovoPointerBlackScreen.ps1"),
  [string]$OutputRoot = "$env:ProgramData\AEGIS\LenovoPointer\BlackScreen"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $CollectorPath)) { throw "Collector not found: $CollectorPath" }
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

$offsets = @(0, 10, 20, 30, 45, 60, 90, 120, 180)
$started = Get-Date
foreach ($offset in $offsets) {
  $target = $started.AddSeconds($offset)
  $remaining = [int][Math]::Ceiling(($target - (Get-Date)).TotalSeconds)
  if ($remaining -gt 0) { Start-Sleep -Seconds $remaining }
  try {
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $CollectorPath -MinutesBack 15 -OutputRoot $OutputRoot | Out-Null
  } catch {
    "$(Get-Date -Format o) collector_error=$($_.Exception.Message)" | Add-Content -LiteralPath (Join-Path $OutputRoot "boot-watch-errors.log") -Encoding UTF8
  }
}
