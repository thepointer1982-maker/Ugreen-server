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
  "Recover-LenovoPointerBlackScreen.ps1",
  "Watch-LenovoPointerBlackScreenBoot.ps1",
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

$installer = Join-Path $Pkg "Install-LenovoPointerBlackScreenWatch.ps1"
$collector = Join-Path $Pkg "Collect-LenovoPointerBlackScreen.ps1"

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($id)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($isAdmin) {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -Action InstallAll
} else {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -Action Install
  Write-Host "AEGIS: Windows will request elevation once to install the pre-login SYSTEM watcher."
  try {
    $argLine = "-NoProfile -ExecutionPolicy Bypass -File `"$installer`" -Action InstallBoot"
    $p = Start-Process powershell.exe -Verb RunAs -ArgumentList $argLine -Wait -PassThru
    if ($p.ExitCode -ne 0) {
      Write-Warning "Boot watcher elevation returned exit code $($p.ExitCode). User logon watcher remains active."
    }
  } catch {
    Write-Warning "Boot watcher was not installed: $($_.Exception.Message). User logon watcher remains active."
  }
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $collector -MinutesBack 30

$statusJson = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -Action Status

[pscustomobject]@{
  status = "installed"
  trusted_sha = $TrustedSha
  package = $Pkg
  user_evidence_root = $Root
  boot_evidence_root = "$env:ProgramData\AEGIS\LenovoPointer\BlackScreen"
  watcher_status = $(try { $statusJson | ConvertFrom-Json } catch { $statusJson })
  next = "No reboot is forced. On the next startup, the SYSTEM watcher captures pre-login Hello/USB/display evidence; the logon watcher captures the user session."
} | ConvertTo-Json -Depth 8
