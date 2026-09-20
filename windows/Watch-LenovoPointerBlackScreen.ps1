param(
  [string]$CollectorPath = (Join-Path $PSScriptRoot "Collect-LenovoPointerBlackScreen.ps1")
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $CollectorPath)) {
  throw "Collector not found: $CollectorPath"
}

$delays = @(0, 30, 60, 90)
foreach ($delay in $delays) {
  if ($delay -gt 0) { Start-Sleep -Seconds $delay }
  try {
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $CollectorPath -MinutesBack 15 | Out-Null
  } catch {
    $logRoot = Join-Path $env:LOCALAPPDATA "AEGIS\LenovoPointer\BlackScreen"
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    "$(Get-Date -Format o) collector_error=$($_.Exception.Message)" | Add-Content -LiteralPath (Join-Path $logRoot "watch-errors.log") -Encoding UTF8
  }
}
