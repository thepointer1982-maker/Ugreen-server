param(
  [ValidateSet("Status","PlanEchoIsolation","ApplyEchoIsolation","RestoreEcho","DisableFastStartup","RestoreFastStartup","RestartExplorer")]
  [string]$Action = "Status"
)

$ErrorActionPreference = "Stop"
$Root = Join-Path $env:LOCALAPPDATA "AEGIS\LenovoPointer\BlackScreen"
$StateDir = Join-Path $Root "repair-state"
$EchoState = Join-Path $StateDir "echo-isolation.json"
$FastState = Join-Path $StateDir "fast-startup.json"
New-Item -ItemType Directory -Path $StateDir -Force | Out-Null

function Test-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $p = New-Object Security.Principal.WindowsPrincipal($id)
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Require-Admin {
  if (-not (Test-Admin)) { throw "This action requires an elevated PowerShell session." }
}

function Get-EchoDevices {
  if (-not (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue)) { return @() }
  @(Get-PnpDevice -ErrorAction SilentlyContinue |
    Where-Object { $_.FriendlyName -match "(?i)Echo Studio|Amazon Echo|Echo" } |
    Select-Object Status, Class, FriendlyName, InstanceId, Problem)
}

function Get-FastStartupValue {
  $path = "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power"
  try {
    return (Get-ItemProperty -Path $path -Name HiberbootEnabled -ErrorAction Stop).HiberbootEnabled
  } catch {
    return $null
  }
}

function Write-State {
  param([string]$Path, $Value)
  $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
}

if ($Action -eq "Status" -or $Action -eq "PlanEchoIsolation") {
  [pscustomobject]@{
    action = $Action
    admin = (Test-Admin)
    echo_candidates = @(Get-EchoDevices)
    echo_backup_exists = (Test-Path -LiteralPath $EchoState)
    hiberboot_enabled = (Get-FastStartupValue)
    fast_startup_backup_exists = (Test-Path -LiteralPath $FastState)
    safety = "No changes performed by Status/PlanEchoIsolation"
  } | ConvertTo-Json -Depth 8
  exit 0
}

if ($Action -eq "ApplyEchoIsolation") {
  Require-Admin
  if (-not (Get-Command Disable-PnpDevice -ErrorAction SilentlyContinue)) { throw "Disable-PnpDevice is unavailable." }
  if (Test-Path -LiteralPath $EchoState) {
    $existing = Get-Content -LiteralPath $EchoState -Raw | ConvertFrom-Json
    if ($existing.active -eq $true) {
      [pscustomobject]@{ status="already-applied"; backup=$EchoState; device_count=@($existing.devices).Count } | ConvertTo-Json
      exit 0
    }
  }
  $devices = @(Get-EchoDevices)
  if ($devices.Count -eq 0) { throw "No Echo-like PnP endpoints were found. Nothing changed." }
  $state = [ordered]@{
    schema = "aegis-echo-isolation/v1"
    applied_at = (Get-Date).ToString("o")
    active = $true
    devices = @($devices)
  }
  Write-State $EchoState $state
  foreach ($d in $devices) {
    if ($d.Status -eq "OK") {
      Disable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction Stop
    }
  }
  [pscustomobject]@{ status="applied"; backup=$EchoState; device_count=$devices.Count } | ConvertTo-Json
  exit 0
}

if ($Action -eq "RestoreEcho") {
  Require-Admin
  if (-not (Get-Command Enable-PnpDevice -ErrorAction SilentlyContinue)) { throw "Enable-PnpDevice is unavailable." }
  if (-not (Test-Path -LiteralPath $EchoState)) { throw "Echo isolation backup not found." }
  $state = Get-Content -LiteralPath $EchoState -Raw | ConvertFrom-Json
  foreach ($d in @($state.devices)) {
    if ($d.Status -eq "OK" -and $d.InstanceId) {
      Enable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction Continue
    }
  }
  $state.active = $false
  $state | Add-Member -NotePropertyName restored_at -NotePropertyValue (Get-Date).ToString("o") -Force
  Write-State $EchoState $state
  [pscustomobject]@{ status="restored"; backup=$EchoState } | ConvertTo-Json
  exit 0
}

if ($Action -eq "DisableFastStartup") {
  Require-Admin
  $path = "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power"
  if (Test-Path -LiteralPath $FastState) {
    $existing = Get-Content -LiteralPath $FastState -Raw | ConvertFrom-Json
    if ($existing.active -eq $true) {
      Set-ItemProperty -Path $path -Name HiberbootEnabled -Type DWord -Value 0
      [pscustomobject]@{ status="already-disabled"; original=$existing.original_value; backup=$FastState } | ConvertTo-Json
      exit 0
    }
  }
  $current = Get-FastStartupValue
  $state = [ordered]@{
    schema = "aegis-fast-startup/v1"
    changed_at = (Get-Date).ToString("o")
    active = $true
    original_value = $current
  }
  Write-State $FastState $state
  Set-ItemProperty -Path $path -Name HiberbootEnabled -Type DWord -Value 0
  [pscustomobject]@{ status="disabled"; previous=$current; backup=$FastState } | ConvertTo-Json
  exit 0
}

if ($Action -eq "RestoreFastStartup") {
  Require-Admin
  if (-not (Test-Path -LiteralPath $FastState)) { throw "Fast Startup backup not found." }
  $path = "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power"
  $state = Get-Content -LiteralPath $FastState -Raw | ConvertFrom-Json
  if ($null -eq $state.original_value) {
    Remove-ItemProperty -Path $path -Name HiberbootEnabled -ErrorAction SilentlyContinue
  } else {
    Set-ItemProperty -Path $path -Name HiberbootEnabled -Type DWord -Value ([int]$state.original_value)
  }
  $state.active = $false
  $state | Add-Member -NotePropertyName restored_at -NotePropertyValue (Get-Date).ToString("o") -Force
  Write-State $FastState $state
  [pscustomobject]@{ status="restored"; restored_value=$state.original_value; backup=$FastState } | ConvertTo-Json
  exit 0
}

if ($Action -eq "RestartExplorer") {
  Get-Process explorer -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Process explorer.exe
  [pscustomobject]@{ status="explorer-restarted"; changed_at=(Get-Date).ToString("o") } | ConvertTo-Json
  exit 0
}
