param(
  [int]$MinutesBack = 30,
  [string]$OutputRoot = "$env:LOCALAPPDATA\AEGIS\LenovoPointer\BlackScreen"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outDir = Join-Path $OutputRoot $stamp
New-Item -ItemType Directory -Path $outDir -Force | Out-Null
$captureTime = Get-Date
$since = $captureTime.AddMinutes(-1 * [Math]::Abs($MinutesBack))
$correlationWindowSeconds = 120

function Write-JsonFile {
  param([string]$Path, $Value)
  $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Safe-Run {
  param([scriptblock]$Block, [string]$Name)
  try { & $Block } catch {
    [pscustomobject]@{ error = $_.Exception.Message; source = $Name }
  }
}

$computer = Safe-Run {
  $os = Get-CimInstance Win32_OperatingSystem
  $cs = Get-CimInstance Win32_ComputerSystem
  [pscustomobject]@{
    collected_at = (Get-Date).ToString("o")
    computer_name = $env:COMPUTERNAME
    windows_caption = $os.Caption
    windows_version = $os.Version
    windows_build = $os.BuildNumber
    last_boot = $os.LastBootUpTime
    manufacturer = $cs.Manufacturer
    model = $cs.Model
  }
} "computer"
Write-JsonFile (Join-Path $outDir "computer.json") $computer

$video = Safe-Run {
  Get-CimInstance Win32_VideoController | Select-Object Name, AdapterCompatibility, DriverVersion, DriverDate, PNPDeviceID, Status, CurrentHorizontalResolution, CurrentVerticalResolution, CurrentRefreshRate
} "video"
Write-JsonFile (Join-Path $outDir "video.json") $video

$displayPnp = Safe-Run {
  if (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue) {
    Get-PnpDevice -Class Display -ErrorAction SilentlyContinue | Select-Object Status, Class, FriendlyName, InstanceId, Problem
  }
} "display-pnp"
Write-JsonFile (Join-Path $outDir "display-pnp.json") $displayPnp

$btNet = Safe-Run {
  if (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue) {
    Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
      Where-Object { $_.Class -in @("Bluetooth","Net","AudioEndpoint","Media") -or $_.FriendlyName -match "(?i)Echo|Bluetooth|Wireless|Wi-Fi|WLAN" } |
      Select-Object Status, Class, FriendlyName, InstanceId, Problem
  }
} "bluetooth-network-pnp"
Write-JsonFile (Join-Path $outDir "bluetooth-network-pnp.json") $btNet

$netAdapters = Safe-Run {
  if (Get-Command Get-NetAdapter -ErrorAction SilentlyContinue) {
    Get-NetAdapter -IncludeHidden | Select-Object Name, InterfaceDescription, Status, LinkSpeed, MediaConnectionState, DriverInformation
  }
} "net-adapters"
Write-JsonFile (Join-Path $outDir "net-adapters.json") $netAdapters

$netProfiles = Safe-Run {
  if (Get-Command Get-NetConnectionProfile -ErrorAction SilentlyContinue) {
    Get-NetConnectionProfile | Select-Object InterfaceAlias, InterfaceIndex, NetworkCategory, IPv4Connectivity, IPv6Connectivity
  }
} "net-profiles"
Write-JsonFile (Join-Path $outDir "net-profiles.json") $netProfiles

$biometricPnp = Safe-Run {
  if (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue) {
    Get-PnpDevice -Class Biometric -ErrorAction SilentlyContinue |
      Select-Object Status, Class, FriendlyName, InstanceId, Problem
  }
} "biometric-pnp"
Write-JsonFile (Join-Path $outDir "biometric-pnp.json") $biometricPnp

$usbApplePnp = Safe-Run {
  if (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue) {
    Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
      Where-Object {
        $_.Class -in @("USB","Biometric") -or
        $_.FriendlyName -match "(?i)Apple|iPhone|Fingerprint|Biometric|USB-C|USB "
      } |
      Select-Object Status, Class, FriendlyName, InstanceId, Problem
  }
} "usb-apple-pnp"
Write-JsonFile (Join-Path $outDir "usb-apple-pnp.json") $usbApplePnp

$biometricService = Safe-Run {
  Get-CimInstance Win32_Service -Filter "Name='WbioSrvc'" -ErrorAction SilentlyContinue |
    Select-Object Name, State, StartMode, Status, ProcessId
} "biometric-service"
Write-JsonFile (Join-Path $outDir "biometric-service.json") $biometricService

$deviceGuard = Safe-Run {
  Get-CimInstance -Namespace "root\Microsoft\Windows\DeviceGuard" -ClassName Win32_DeviceGuard -ErrorAction SilentlyContinue |
    Select-Object VirtualizationBasedSecurityStatus, SecurityServicesConfigured, SecurityServicesRunning
} "device-guard"
Write-JsonFile (Join-Path $outDir "device-guard.json") $deviceGuard

$winlogonConfig = Safe-Run {
  $path = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
  Get-ItemProperty -Path $path -ErrorAction Stop |
    Select-Object Shell, Userinit
} "winlogon-config"
Write-JsonFile (Join-Path $outDir "winlogon-config.json") $winlogonConfig

$audioEndpoints = Safe-Run {
  if (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue) {
    Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
      Where-Object {
        $_.Class -in @("AudioEndpoint","Media") -or
        ($_.Class -eq "Bluetooth" -and $_.FriendlyName -match "(?i)Echo|Headset|Headphone")
      } |
      Select-Object Status, Class, FriendlyName, InstanceId, Problem
  }
} "audio-endpoints"
Write-JsonFile (Join-Path $outDir "audio-endpoints.json") $audioEndpoints

$credentialProviders = Safe-Run {
  $root = "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers"
  if (Test-Path $root) {
    Get-ChildItem -Path $root -ErrorAction SilentlyContinue | ForEach-Object {
      $guid = $_.PSChildName
      $item = Get-Item -LiteralPath $_.PSPath -ErrorAction SilentlyContinue
      $name = $(if ($item) { $item.GetValue("") } else { $null })
      $clsidPath = "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Classes\CLSID\$guid\InprocServer32"
      $dll = $null
      if (Test-Path $clsidPath) {
        $dllItem = Get-Item -LiteralPath $clsidPath -ErrorAction SilentlyContinue
        if ($dllItem) { $dll = $dllItem.GetValue("") }
      }
      $signatureStatus = $null
      $signer = $null
      if ($dll -and (Test-Path -LiteralPath $dll)) {
        $sig = Get-AuthenticodeSignature -FilePath $dll -ErrorAction SilentlyContinue
        if ($sig) {
          $signatureStatus = [string]$sig.Status
          if ($sig.SignerCertificate) { $signer = $sig.SignerCertificate.Subject }
        }
      }
      [pscustomobject]@{
        Guid = $guid
        Name = $name
        Dll = $dll
        SignatureStatus = $signatureStatus
        Signer = $signer
      }
    }
  }
} "credential-providers"
Write-JsonFile (Join-Path $outDir "credential-providers.json") $credentialProviders

$credentialProviderFilters = Safe-Run {
  $root = "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Provider Filters"
  if (Test-Path $root) {
    Get-ChildItem -Path $root -ErrorAction SilentlyContinue | ForEach-Object {
      $guid = $_.PSChildName
      $item = Get-Item -LiteralPath $_.PSPath -ErrorAction SilentlyContinue
      $name = $(if ($item) { $item.GetValue("") } else { $null })
      $clsidPath = "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Classes\CLSID\$guid\InprocServer32"
      $dll = $null
      if (Test-Path $clsidPath) {
        $dllItem = Get-Item -LiteralPath $clsidPath -ErrorAction SilentlyContinue
        if ($dllItem) { $dll = $dllItem.GetValue("") }
      }
      $signatureStatus = $null
      $signer = $null
      if ($dll -and (Test-Path -LiteralPath $dll)) {
        $sig = Get-AuthenticodeSignature -FilePath $dll -ErrorAction SilentlyContinue
        if ($sig) {
          $signatureStatus = [string]$sig.Status
          if ($sig.SignerCertificate) { $signer = $sig.SignerCertificate.Subject }
        }
      }
      [pscustomobject]@{
        Guid = $guid
        Name = $name
        Dll = $dll
        SignatureStatus = $signatureStatus
        Signer = $signer
      }
    }
  }
} "credential-provider-filters"
Write-JsonFile (Join-Path $outDir "credential-provider-filters.json") $credentialProviderFilters

$processes = Safe-Run {
  Get-Process -Name bioiso,ngciso,LogonUI,winlogon,userinit,dwm,explorer,codex,node,python,python3,pwsh,powershell -ErrorAction SilentlyContinue |
    Select-Object Name, Id, StartTime, Responding, CPU, WorkingSet64
} "session-processes"
Write-JsonFile (Join-Path $outDir "session-processes.json") $processes

$helloEvents = @()
foreach ($logName in @(
  "Microsoft-Windows-Biometrics/Operational",
  "Microsoft-Windows-Winlogon/Operational"
)) {
  $log = Get-WinEvent -ListLog $logName -ErrorAction SilentlyContinue
  if ($log) {
    $chunk = Safe-Run {
      Get-WinEvent -FilterHashtable @{ LogName=$logName; StartTime=$since } -ErrorAction SilentlyContinue |
        Select-Object TimeCreated, LogName, ProviderName, Id, LevelDisplayName, Message
    } "events-$logName"
    if ($chunk) { $helloEvents += $chunk }
  }
}
Write-JsonFile (Join-Path $outDir "hello-winlogon-events.json") $helloEvents

$fastStartup = Safe-Run {
  $p = "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power"
  $v = (Get-ItemProperty -Path $p -Name HiberbootEnabled -ErrorAction SilentlyContinue).HiberbootEnabled
  [pscustomobject]@{ HiberbootEnabled = $v }
} "fast-startup"
Write-JsonFile (Join-Path $outDir "fast-startup.json") $fastStartup

Safe-Run { powercfg /a } "powercfg-a" | Out-File -LiteralPath (Join-Path $outDir "powercfg-a.txt") -Encoding utf8
Safe-Run { pnputil /enum-devices /class Display } "pnputil-display" | Out-File -LiteralPath (Join-Path $outDir "pnputil-display.txt") -Encoding utf8
Safe-Run { pnputil /enum-devices /class Bluetooth } "pnputil-bluetooth" | Out-File -LiteralPath (Join-Path $outDir "pnputil-bluetooth.txt") -Encoding utf8

$providers = "Display|nvlddmkm|amdwddmg|igfx|Kernel-PnP|Kernel-Power|BTHUSB|Bluetooth|WLAN|Netwtw|NDIS|Tcpip|UserPnp|USBHUB3|USBXHCI|Desktop Window Manager|Application Error|Windows Error Reporting"
$events = @()
foreach ($logName in @("System","Application")) {
  $chunk = Safe-Run {
    Get-WinEvent -FilterHashtable @{ LogName=$logName; StartTime=$since } -ErrorAction SilentlyContinue |
      Where-Object { $_.ProviderName -match $providers -or $_.Id -in @(41,1000,1001,4101,6008) } |
      Select-Object TimeCreated, LogName, ProviderName, Id, LevelDisplayName, Message
  } "events-$logName"
  if ($chunk) { $events += $chunk }
}
Write-JsonFile (Join-Path $outDir "events.json") $events

$timeline = @()
foreach ($e in @($helloEvents)) {
  if ($e.TimeCreated) {
    $category = $(if ($e.LogName -eq "Microsoft-Windows-Biometrics/Operational") { "biometric" } else { "winlogon" })
    $timeline += [pscustomobject]@{
      time = ([datetime]$e.TimeCreated).ToString("o")
      seconds_before_capture = [Math]::Round(($captureTime - [datetime]$e.TimeCreated).TotalSeconds, 3)
      category = $category
      provider = [string]$e.ProviderName
      event_id = [int]$e.Id
      level = [string]$e.LevelDisplayName
    }
  }
}
foreach ($e in @($events)) {
  if ($e.TimeCreated) {
    $category = "other"
    if ($e.Id -eq 4101 -or $e.ProviderName -match "(?i)Display|nvlddmkm|amdwddmg|igfx") { $category = "display-gpu" }
    elseif ($e.ProviderName -match "(?i)Desktop Window Manager|Application Error|Windows Error Reporting" -and $e.Message -match "(?i)dwm\.exe|explorer\.exe|Desktop Window Manager") { $category = "shell-dwm" }
    elseif ($e.ProviderName -match "(?i)BTHUSB|Bluetooth|WLAN|Netwtw|NDIS") { $category = "bluetooth-wifi" }
    elseif ($e.ProviderName -match "(?i)USBHUB3|USBXHCI|Kernel-PnP") { $category = "usb-pnp" }
    $timeline += [pscustomobject]@{
      time = ([datetime]$e.TimeCreated).ToString("o")
      seconds_before_capture = [Math]::Round(($captureTime - [datetime]$e.TimeCreated).TotalSeconds, 3)
      category = $category
      provider = [string]$e.ProviderName
      event_id = [int]$e.Id
      level = [string]$e.LevelDisplayName
    }
  }
}
$timeline = @($timeline | Sort-Object { [datetime]$_.time })
Write-JsonFile (Join-Path $outDir "correlation-timeline.json") $timeline

$recentTimeline = @($timeline | Where-Object {
  $_.seconds_before_capture -ge 0 -and $_.seconds_before_capture -le $correlationWindowSeconds
})
$recentBiometricErrors = @($recentTimeline | Where-Object {
  $_.category -eq "biometric" -and $_.level -match "(?i)Error|Critical|Warning"
}).Count
$recentDisplaySignals = @($recentTimeline | Where-Object { $_.category -eq "display-gpu" }).Count
$recentShellSignals = @($recentTimeline | Where-Object { $_.category -eq "shell-dwm" }).Count
$recentBtWifiErrors = @($recentTimeline | Where-Object {
  $_.category -eq "bluetooth-wifi" -and $_.level -match "(?i)Error|Critical|Warning"
}).Count
$recentUsbErrors = @($recentTimeline | Where-Object {
  $_.category -eq "usb-pnp" -and $_.level -match "(?i)Error|Critical|Warning"
}).Count

$reliability = Safe-Run {
  Get-CimInstance Win32_ReliabilityRecords -ErrorAction SilentlyContinue |
    Where-Object { $_.TimeGenerated -ge $since } |
    Select-Object TimeGenerated, SourceName, ProductName, EventIdentifier, Message
} "reliability"
Write-JsonFile (Join-Path $outDir "reliability.json") $reliability

$liveKernel = Safe-Run {
  $root = "C:\Windows\LiveKernelReports"
  if (Test-Path $root) {
    Get-ChildItem -Path $root -File -Recurse -ErrorAction SilentlyContinue |
      Where-Object { $_.LastWriteTime -ge $since } |
      Select-Object FullName, Length, LastWriteTime
  }
} "live-kernel-reports"
Write-JsonFile (Join-Path $outDir "live-kernel-reports.json") $liveKernel

$display4101 = @($events | Where-Object { $_.Id -eq 4101 -or $_.ProviderName -eq "Display" }).Count
$dwmCrashes = @($events | Where-Object { $_.Message -match "(?i)dwm\.exe|Desktop Window Manager" }).Count
$explorerCrashes = @($events | Where-Object { $_.Message -match "(?i)explorer\.exe" }).Count
$btWifiErrors = @($events | Where-Object { $_.ProviderName -match "(?i)BTHUSB|Bluetooth|WLAN|Netwtw|NDIS" -and $_.LevelDisplayName -match "(?i)Error|Critical|Warning" }).Count
$echoEndpoints = @($btNet | Where-Object { $_.FriendlyName -match "(?i)Echo|Amazon" }).Count
$liveKernelCount = @($liveKernel).Count
$biometric1108 = @($helloEvents | Where-Object { $_.LogName -eq "Microsoft-Windows-Biometrics/Operational" -and $_.Id -eq 1108 }).Count
$biometricErrors = @($helloEvents | Where-Object { $_.LogName -eq "Microsoft-Windows-Biometrics/Operational" -and $_.LevelDisplayName -match "(?i)Error|Critical|Warning" }).Count
$biometricEndpoints = @($biometricPnp).Count
$helloIsolationProcesses = @($processes | Where-Object { $_.Name -in @("bioiso","ngciso") }).Count
$codexProcesses = @($processes | Where-Object { $_.Name -eq "codex" }).Count
$usbAppleDevices = @($usbApplePnp | Where-Object { $_.FriendlyName -match "(?i)Apple|iPhone" }).Count
$usbErrors = @($events | Where-Object { $_.ProviderName -match "(?i)USBHUB3|USBXHCI|Kernel-PnP" -and $_.LevelDisplayName -match "(?i)Error|Critical|Warning" }).Count
$echoAudioEndpoints = @($audioEndpoints | Where-Object { $_.FriendlyName -match "(?i)Echo Studio|Amazon Echo|Echo" }).Count
$headsetAudioEndpoints = @($audioEndpoints | Where-Object { $_.FriendlyName -match "(?i)Headset|Headphone|Kopfhörer|Realtek|USB Audio" }).Count
$credentialProviderCount = @($credentialProviders).Count
$credentialProviderFilterCount = @($credentialProviderFilters).Count
$invalidCredentialProviderSignatures = @(
  $credentialProviders | Where-Object {
    $_.Dll -and $_.SignatureStatus -and $_.SignatureStatus -ne "Valid"
  }
).Count
$invalidCredentialProviderFilterSignatures = @(
  $credentialProviderFilters | Where-Object {
    $_.Dll -and $_.SignatureStatus -and $_.SignatureStatus -ne "Valid"
  }
).Count
$shellConfigured = [string]$winlogonConfig.Shell
$userinitConfigured = [string]$winlogonConfig.Userinit
$shellConfigMismatch = 0
if ($shellConfigured -and $shellConfigured -notmatch "(?i)^explorer\.exe$") { $shellConfigMismatch++ }
if ($userinitConfigured -and $userinitConfigured -notmatch "(?i)userinit\.exe") { $shellConfigMismatch++ }

$scores = [ordered]@{
  windows_hello_biometrics = [Math]::Min(100, ($recentBiometricErrors * 35) + ($invalidCredentialProviderSignatures * 30) + ($invalidCredentialProviderFilterSignatures * 30))
  gpu_display_driver = [Math]::Min(100, ($recentDisplaySignals * 45) + ($liveKernelCount * 25))
  shell_dwm_explorer = [Math]::Min(100, ($recentShellSignals * 40) + ($shellConfigMismatch * 35))
  bluetooth_wifi_combo = [Math]::Min(100, ($recentBtWifiErrors * 25) + ($(if ($echoEndpoints -gt 0 -and $recentBtWifiErrors -gt 0) { 10 } else { 0 })))
  usb_c_apple_path = [Math]::Min(100, ($recentUsbErrors * 20) + ($(if ($usbAppleDevices -gt 0 -and $recentUsbErrors -gt 0) { 10 } else { 0 })))
  fast_startup_candidate = $(if ($fastStartup.HiberbootEnabled -eq 1) { 20 } else { 0 })
}
$ranked = @($scores.GetEnumerator() | Sort-Object Value -Descending)
$topScore = $(if ($ranked.Count -gt 0) { [int]$ranked[0].Value } else { 0 })
$summary = [ordered]@{
  schema = "aegis-lenovopointer-black-screen/v1"
  collected_at = (Get-Date).ToString("o")
  minutes_back = $MinutesBack
  output_dir = $outDir
  evidence = [ordered]@{
    display_4101_or_display_events = $display4101
    live_kernel_reports = $liveKernelCount
    dwm_events = $dwmCrashes
    explorer_events = $explorerCrashes
    bluetooth_wifi_warning_error_events = $btWifiErrors
    echo_like_pnp_endpoints = $echoEndpoints
    biometric_event_1108 = $biometric1108
    biometric_warning_error_events = $biometricErrors
    biometric_pnp_endpoints = $biometricEndpoints
    hello_isolation_processes = $helloIsolationProcesses
    codex_processes_alive = $codexProcesses
    apple_iphone_usb_devices = $usbAppleDevices
    usb_pnp_warning_error_events = $usbErrors
    echo_audio_endpoint_count = $echoAudioEndpoints
    headset_audio_endpoint_count = $headsetAudioEndpoints
    credential_provider_count = $credentialProviderCount
    credential_provider_filter_count = $credentialProviderFilterCount
    invalid_credential_provider_signature_count = $invalidCredentialProviderSignatures
    invalid_credential_provider_filter_signature_count = $invalidCredentialProviderFilterSignatures
    biometric_recent_warning_error_120s = $recentBiometricErrors
    display_gpu_recent_signal_120s = $recentDisplaySignals
    shell_dwm_recent_signal_120s = $recentShellSignals
    bluetooth_wifi_recent_warning_error_120s = $recentBtWifiErrors
    usb_pnp_recent_warning_error_120s = $recentUsbErrors
    correlation_window_seconds = $correlationWindowSeconds
    winlogon_shell_mismatch_count = $shellConfigMismatch
    hiberboot_enabled = $fastStartup.HiberbootEnabled
  }
  hypothesis_scores = $scores
  leading_hypothesis = $(if ($topScore -gt 0) { $ranked[0].Name } else { "insufficient-evidence" })
  score_method = "Only temporally correlated warning/error signals, invalid signatures, shell mismatch, live-kernel evidence, and Fast Startup state contribute. Event 1108 and the mere presence of credential-provider filters are informational only."
  interpretation = "Codex, Echo, fingerprint Event 1108, credential-provider filters, and USB-C presence are correlation evidence only; none is treated as causal without time-correlated errors or integrity failures."
  safety = "collect-only; no drivers, boot, partitions, firmware, biometric enrollment, Codex process, or device state changed"
}
Write-JsonFile (Join-Path $outDir "summary.json") $summary
$summary | ConvertTo-Json -Depth 6
