param(
  [ValidateSet("Status","PlanEchoIsolation","ApplyEchoIsolation","RestoreEcho","PlanEchoAudioIsolation","ApplyEchoAudioIsolation","RestoreEchoAudio","PlanFingerprintIsolation","ApplyFingerprintIsolation","RestoreFingerprint","RestartBiometricService","DisableFastStartup","RestoreFastStartup","RestartExplorer")]
  [string]$Action = "Status",
  [switch]$ConfirmAlternativeSignIn
)

$ErrorActionPreference = "Stop"
$Root = Join-Path $env:LOCALAPPDATA "AEGIS\LenovoPointer\BlackScreen"
$StateDir = Join-Path $Root "repair-state"
$EchoState = Join-Path $StateDir "echo-isolation.json"
$EchoAudioState = Join-Path $StateDir "echo-audio-isolation.json"
$FingerprintState = Join-Path $StateDir "fingerprint-isolation.json"
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

function Get-EchoAudioDevices {
  if (-not (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue)) { return @() }
  @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
    Where-Object {
      $_.FriendlyName -match "(?i)Echo Studio|Amazon Echo|Echo" -and
      $_.Class -in @("AudioEndpoint","Media","Bluetooth")
    } |
    Select-Object Status, Class, FriendlyName, InstanceId, Problem)
}

function Get-FingerprintDevices {
  if (-not (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue)) { return @() }
  @(Get-PnpDevice -Class Biometric -ErrorAction SilentlyContinue |
    Where-Object { $_.FriendlyName -match "(?i)finger|goodix|synaptics|elan|fpc|wbf|biometric" } |
    Select-Object Status, Class, FriendlyName, InstanceId, Problem)
}

function Get-FingerprintDrivers {
  @(Get-CimInstance Win32_PnPSignedDriver -ErrorAction SilentlyContinue |
    Where-Object { $_.DeviceClass -eq "BIOMETRIC" -or $_.DeviceName -match "(?i)finger|goodix|synaptics|elan|fpc|wbf|biometric" } |
    Select-Object DeviceName, Manufacturer, DriverProviderName, DriverVersion, DriverDate, InfName, DeviceID)
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

if ($Action -eq "Status" -or $Action -eq "PlanEchoIsolation" -or $Action -eq "PlanEchoAudioIsolation" -or $Action -eq "PlanFingerprintIsolation") {
  [pscustomobject]@{
    action = $Action
    admin = (Test-Admin)
    echo_candidates = @(Get-EchoDevices)
    echo_backup_exists = (Test-Path -LiteralPath $EchoState)
    echo_audio_candidates = @(Get-EchoAudioDevices)
    echo_audio_backup_exists = (Test-Path -LiteralPath $EchoAudioState)
    fingerprint_candidates = @(Get-FingerprintDevices)
    fingerprint_drivers = @(Get-FingerprintDrivers)
    fingerprint_backup_exists = (Test-Path -LiteralPath $FingerprintState)
    biometric_service = @(Get-CimInstance Win32_Service -Filter "Name='WbioSrvc'" -ErrorAction SilentlyContinue | Select-Object Name, State, StartMode, Status, ProcessId)
    hiberboot_enabled = (Get-FastStartupValue)
    fast_startup_backup_exists = (Test-Path -LiteralPath $FastState)
    safety = "No changes performed by Status/Plan actions"
  } | ConvertTo-Json -Depth 10
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

if ($Action -eq "ApplyEchoAudioIsolation") {
  Require-Admin
  if (-not (Get-Command Disable-PnpDevice -ErrorAction SilentlyContinue)) { throw "Disable-PnpDevice is unavailable." }
  if (Test-Path -LiteralPath $EchoAudioState) {
    $existing = Get-Content -LiteralPath $EchoAudioState -Raw | ConvertFrom-Json
    if ($existing.active -eq $true) {
      [pscustomobject]@{ status="already-applied"; backup=$EchoAudioState; device_count=@($existing.devices).Count } | ConvertTo-Json
      exit 0
    }
  }

  $devices = @(Get-EchoAudioDevices)
  if ($devices.Count -eq 0) {
    throw "No Echo Studio AudioEndpoint/Media/Bluetooth endpoints were found. Nothing changed."
  }

  $state = [ordered]@{
    schema = "aegis-echo-audio-isolation/v1"
    applied_at = (Get-Date).ToString("o")
    active = $true
    classes = @("AudioEndpoint","Media","Bluetooth")
    devices = @($devices)
  }
  Write-State $EchoAudioState $state

  foreach ($d in $devices) {
    if ($d.Status -eq "OK") {
      Disable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction Stop
    }
  }

  [pscustomobject]@{
    status = "applied"
    backup = $EchoAudioState
    device_count = $devices.Count
    note = "Only Echo-like AudioEndpoint/Media/Bluetooth endpoints were disabled. Wi-Fi, biometric devices, and Codex were not touched."
  } | ConvertTo-Json -Depth 4
  exit 0
}

if ($Action -eq "RestoreEchoAudio") {
  Require-Admin
  if (-not (Get-Command Enable-PnpDevice -ErrorAction SilentlyContinue)) { throw "Enable-PnpDevice is unavailable." }
  if (-not (Test-Path -LiteralPath $EchoAudioState)) { throw "Echo audio isolation backup not found." }

  $state = Get-Content -LiteralPath $EchoAudioState -Raw | ConvertFrom-Json
  foreach ($d in @($state.devices)) {
    if ($d.Status -eq "OK" -and $d.InstanceId) {
      Enable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction Continue
    }
  }

  $state.active = $false
  $state | Add-Member -NotePropertyName restored_at -NotePropertyValue (Get-Date).ToString("o") -Force
  Write-State $EchoAudioState $state

  [pscustomobject]@{ status="restored"; backup=$EchoAudioState } | ConvertTo-Json
  exit 0
}

if ($Action -eq "ApplyFingerprintIsolation") {
  Require-Admin
  if (-not $ConfirmAlternativeSignIn) {
    throw "Blocked: confirm that PIN or password sign-in works, then rerun with -ConfirmAlternativeSignIn."
  }
  if (-not (Get-Command Disable-PnpDevice -ErrorAction SilentlyContinue)) { throw "Disable-PnpDevice is unavailable." }
  if (Test-Path -LiteralPath $FingerprintState) {
    $existing = Get-Content -LiteralPath $FingerprintState -Raw | ConvertFrom-Json
    if ($existing.active -eq $true) {
      [pscustomobject]@{ status="already-applied"; backup=$FingerprintState; device_count=@($existing.devices).Count } | ConvertTo-Json
      exit 0
    }
  }
  $devices = @(Get-FingerprintDevices)
  if ($devices.Count -eq 0) { throw "No fingerprint-like biometric endpoint was found. Nothing changed." }
  $state = [ordered]@{
    schema = "aegis-fingerprint-isolation/v1"
    applied_at = (Get-Date).ToString("o")
    active = $true
    devices = @($devices)
  }
  Write-State $FingerprintState $state
  foreach ($d in $devices) {
    if ($d.Status -eq "OK") {
      Disable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction Stop
    }
  }
  [pscustomobject]@{ status="applied"; backup=$FingerprintState; device_count=$devices.Count; note="Use PIN/password for the next sign-in test." } | ConvertTo-Json
  exit 0
}

if ($Action -eq "RestoreFingerprint") {
  Require-Admin
  if (-not (Get-Command Enable-PnpDevice -ErrorAction SilentlyContinue)) { throw "Enable-PnpDevice is unavailable." }
  if (-not (Test-Path -LiteralPath $FingerprintState)) { throw "Fingerprint isolation backup not found." }
  $state = Get-Content -LiteralPath $FingerprintState -Raw | ConvertFrom-Json
  foreach ($d in @($state.devices)) {
    if ($d.Status -eq "OK" -and $d.InstanceId) {
      Enable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction Continue
    }
  }
  $state.active = $false
  $state | Add-Member -NotePropertyName restored_at -NotePropertyValue (Get-Date).ToString("o") -Force
  Write-State $FingerprintState $state
  [pscustomobject]@{ status="restored"; backup=$FingerprintState } | ConvertTo-Json
  exit 0
}

if ($Action -eq "RestartBiometricService") {
  Require-Admin
  $svc = Get-Service -Name WbioSrvc -ErrorAction Stop
  Restart-Service -Name WbioSrvc -Force -ErrorAction Stop
  [pscustomobject]@{ status="biometric-service-restarted"; previous_status=[string]$svc.Status; changed_at=(Get-Date).ToString("o") } | ConvertTo-Json
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
