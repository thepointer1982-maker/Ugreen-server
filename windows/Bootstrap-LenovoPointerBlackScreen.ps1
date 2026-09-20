param(
  [Parameter(Mandatory=$true)]
  [ValidatePattern("^[0-9a-f]{40}$")]
  [string]$TrustedSha
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Base = "https://raw.githubusercontent.com/thepointer1982-maker/Ugreen-server/$TrustedSha/windows"
$Root = Join-Path $env:LOCALAPPDATA "AEGIS\LenovoPointer\BlackScreen"
$Pkg = Join-Path $Root "package"
New-Item -ItemType Directory -Path $Pkg -Force | Out-Null

$files = @(
  "Collect-LenovoPointerBlackScreen.ps1",
  "Watch-LenovoPointerBlackScreen.ps1",
  "Install-LenovoPointerBlackScreenWatch.ps1",
  "Repair-LenovoPointerBlackScreen.ps1"
)

foreach ($name in $files) {
  $uri = "$Base/$name"
  $dst = Join-Path $Pkg $name
  Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $dst
  if (-not (Test-Path -LiteralPath $dst) -or (Get-Item -LiteralPath $dst).Length -lt 100) {
    throw "Downloaded file is missing or unexpectedly small: $name"
  }
}

$dangerous = @("Clear-Disk","Initialize-Disk","Remove-Partition","Format-Volume","Set-Partition","diskpart","bcdedit","reagentc","pnputil /delete-driver","Remove-PnpDevice")
foreach ($name in $files) {
  $text = Get-Content -LiteralPath (Join-Path $Pkg $name) -Raw
  foreach ($token in $dangerous) {
    if ($text.IndexOf($token, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
      throw "Blocked package: forbidden token $token found in $name"
    }
  }
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Pkg "Install-LenovoPointerBlackScreenWatch.ps1") -Action Install
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Pkg "Collect-LenovoPointerBlackScreen.ps1") -MinutesBack 30

[pscustomobject]@{
  status = "installed"
  trusted_sha = $TrustedSha
  package = $Pkg
  evidence_root = $Root
  next = "Allow the next failing login to occur; the logon watcher will collect evidence automatically."
} | ConvertTo-Json -Depth 4
