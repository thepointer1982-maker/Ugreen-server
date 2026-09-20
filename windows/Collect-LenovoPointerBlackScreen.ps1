param(
  [int]$MinutesBack = 30,
  [string]$OutputRoot = "$env:LOCALAPPDATA\AEGIS\LenovoPointer\BlackScreen"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outDir = Join-Path $OutputRoot $stamp
New-Item -ItemType Directory -Path $outDir -Force | Out-Null
$since = (Get-Date).AddMinutes(-1 * [Math]::Abs($MinutesBack))

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

$processes = Safe-Run {
  Get-Process -Name dwm,explorer -ErrorAction SilentlyContinue | Select-Object Name, Id, StartTime, Responding, CPU, WorkingSet64
} "processes"
Write-JsonFile (Join-Path $outDir "shell-processes.json") $processes

$fastStartup = Safe-Run {
  $p = "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power"
  $v = (Get-ItemProperty -Path $p -Name HiberbootEnabled -ErrorAction SilentlyContinue).HiberbootEnabled
  [pscustomobject]@{ HiberbootEnabled = $v }
} "fast-startup"
Write-JsonFile (Join-Path $outDir "fast-startup.json") $fastStartup

Safe-Run { powercfg /a } "powercfg-a" | Out-File -LiteralPath (Join-Path $outDir "powercfg-a.txt") -Encoding utf8
Safe-Run { pnputil /enum-devices /class Display } "pnputil-display" | Out-File -LiteralPath (Join-Path $outDir "pnputil-display.txt") -Encoding utf8
Safe-Run { pnputil /enum-devices /class Bluetooth } "pnputil-bluetooth" | Out-File -LiteralPath (Join-Path $outDir "pnputil-bluetooth.txt") -Encoding utf8

$providers = "Display|nvlddmkm|amdwddmg|igfx|Kernel-PnP|Kernel-Power|BTHUSB|Bluetooth|WLAN|Netwtw|NDIS|Tcpip|UserPnp|Desktop Window Manager|Application Error|Windows Error Reporting"
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

$scores = [ordered]@{
  gpu_display_driver = [Math]::Min(100, ($display4101 * 35) + ($liveKernelCount * 25))
  shell_dwm_explorer = [Math]::Min(100, ($dwmCrashes * 30) + ($explorerCrashes * 30))
  bluetooth_wifi_combo = [Math]::Min(100, ($btWifiErrors * 15) + ($(if ($echoEndpoints -gt 0) { 20 } else { 0 })))
  fast_startup_candidate = $(if ($fastStartup.HiberbootEnabled -eq 1) { 20 } else { 0 })
}
$ranked = $scores.GetEnumerator() | Sort-Object Value -Descending
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
    hiberboot_enabled = $fastStartup.HiberbootEnabled
  }
  hypothesis_scores = $scores
  leading_hypothesis = $(if ($ranked) { $ranked[0].Name } else { "insufficient-evidence" })
  safety = "collect-only; no drivers, boot, partitions, firmware, or device state changed"
}
Write-JsonFile (Join-Path $outDir "summary.json") $summary
$summary | ConvertTo-Json -Depth 6
