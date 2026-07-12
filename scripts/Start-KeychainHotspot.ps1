[CmdletBinding()]
param(
  [string]$Ssid,
  [string]$Passphrase,
  [string]$Gateway,
  [int]$Port = 8080,
  [switch]$SkipElevation,
  [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot

function Import-DotEnv {
  param([string]$Path)

  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }

  foreach ($rawLine in Get-Content -LiteralPath $Path) {
    $line = $rawLine.Trim()
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
      continue
    }

    $parts = $line.Split("=", 2)
    $key = $parts[0].Trim()
    $value = $parts[1].Trim().Trim('"').Trim("'")
    if ($key -and -not [Environment]::GetEnvironmentVariable($key, "Process")) {
      [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
  }
}

function Test-IsAdmin {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-ElevatedSelf {
  $argumentList = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$PSCommandPath`"",
    "-Ssid", "`"$Ssid`"",
    "-Passphrase", "`"$Passphrase`"",
    "-Gateway", "`"$Gateway`"",
    "-Port", "$Port",
    "-SkipElevation"
  )
  $process = Start-Process -FilePath "powershell.exe" -ArgumentList $argumentList -Verb RunAs -PassThru
  $process.WaitForExit()
  exit $process.ExitCode
}

function Initialize-WinRtAsync {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime

  $script:AsTaskAction = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
      $_.Name -eq "AsTask" -and
      $_.GetParameters().Count -eq 1 -and
      $_.GetParameters()[0].ParameterType.FullName -eq "Windows.Foundation.IAsyncAction"
    } |
    Select-Object -First 1

  $script:AsTaskOperation = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
      $_.Name -eq "AsTask" -and
      $_.GetParameters().Count -eq 1 -and
      $_.GetParameters()[0].ParameterType.IsGenericType -and
      $_.GetParameters()[0].ParameterType.GetGenericTypeDefinition().FullName -eq 'Windows.Foundation.IAsyncOperation`1'
    } |
    Select-Object -First 1

  if (-not $script:AsTaskAction -or -not $script:AsTaskOperation) {
    throw "Windows Runtime async helpers are unavailable."
  }
}

function Wait-IAsyncAction {
  param([object]$Operation)

  $task = $script:AsTaskAction.Invoke($null, @($Operation))
  try {
    $task.Wait()
  } catch {
    if ($task.Exception -and $task.Exception.InnerException) {
      throw $task.Exception.InnerException
    }
    throw
  }
}

function Wait-IAsyncOperation {
  param(
    [object]$Operation,
    [Type]$ResultType
  )

  $method = $script:AsTaskOperation.MakeGenericMethod($ResultType)
  $task = $method.Invoke($null, @($Operation))
  try {
    $task.Wait()
  } catch {
    if ($task.Exception -and $task.Exception.InnerException) {
      throw $task.Exception.InnerException
    }
    throw
  }
  return $task.Result
}

function Ensure-FirewallRule {
  param(
    [int]$LocalPort,
    [string]$HotspotGateway
  )

  $ruleName = "Keychain Fair HTTP $LocalPort"
  $offlineRuleName = "Keychain Fair clients offline"
  $existing = @(Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)
  if ($existing.Count -gt 0) {
    $existing | Remove-NetFirewallRule
  }

  $existingOffline = @(Get-NetFirewallRule -DisplayName $offlineRuleName -ErrorAction SilentlyContinue)
  if ($existingOffline.Count -gt 0) {
    $existingOffline | Remove-NetFirewallRule
  }

  New-NetFirewallRule `
    -DisplayName $ruleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $LocalPort `
    -Profile Any | Out-Null

  $prefix = $HotspotGateway -replace '\.\d+$', '.0/24'
  New-NetFirewallRule `
    -DisplayName $offlineRuleName `
    -Direction Outbound `
    -Action Block `
    -Protocol Any `
    -LocalAddress $prefix `
    -RemoteAddress Internet `
    -Profile Any | Out-Null
}

function Start-HotspotWithRetry {
  param(
    [object]$Manager,
    [Type]$ResultType,
    [int]$Attempts = 3
  )

  for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
    if ("$($Manager.TetheringOperationalState)" -eq "On") {
      return
    }

    Write-Host "Starting Mobile Hotspot '$Ssid' (attempt $attempt/$Attempts)..."
    $result = Wait-IAsyncOperation ($Manager.StartTetheringAsync()) $ResultType
    if ("$($result.Status)" -eq "Success") {
      return
    }

    Write-Warning "Mobile Hotspot start attempt failed: $($result.Status) $($result.AdditionalErrorMessage)"
    Start-Sleep -Seconds 2
  }

  throw "Mobile Hotspot did not reach the On state after $Attempts attempts."
}

function Wait-GatewayAddress {
  param(
    [string]$Address,
    [int]$Attempts = 30
  )

  for ($i = 0; $i -lt $Attempts; $i++) {
    $match = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $Address -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($match) {
      return $match
    }
    Start-Sleep -Milliseconds 500
  }

  throw "Hotspot gateway $Address was not assigned. Printed QR would not work."
}

function Disconnect-UpstreamWifi {
  param([string]$StatePath)

  $wifiAdapter = Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
    Where-Object { $_.Status -eq "Up" -and $_.InterfaceDescription -match "Wi-?Fi|Wireless|802\.11" } |
    Select-Object -First 1
  if (-not $wifiAdapter) {
    return
  }

  $interfaceDetails = & netsh wlan show interfaces
  $profileLine = $interfaceDetails | Where-Object { $_ -match '^\s+Profile\s*:\s*(.+)$' } | Select-Object -First 1
  if (-not $profileLine) {
    return
  }
  $profileName = ([regex]::Match($profileLine, '^\s+Profile\s*:\s*(.+)$')).Groups[1].Value.Trim()

  Write-Host "Disconnecting upstream Wi-Fi '$profileName' for offline operation..."
  & netsh wlan set profileparameter name="$profileName" connectionmode=manual | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Could not disable automatic connection for Wi-Fi profile '$profileName'."
  }
  Set-Content -LiteralPath $StatePath -Value $profileName
  & netsh wlan disconnect | Out-Null
  Start-Sleep -Seconds 2
}

Import-DotEnv (Join-Path $Root ".env")

if (-not $Ssid) { $Ssid = $env:KEYCHAIN_WIFI_SSID }
if (-not $Passphrase) { $Passphrase = $env:KEYCHAIN_WIFI_PASSWORD }
if (-not $Gateway) { $Gateway = $env:KEYCHAIN_HOTSPOT_GATEWAY }
if (-not $Ssid) { $Ssid = "KeychainFair" }
if (-not $Passphrase) { $Passphrase = "fair2026" }
if (-not $Gateway) { $Gateway = "192.168.137.1" }

if ($Passphrase.Length -lt 8 -or $Passphrase.Length -gt 63) {
  throw "Wi-Fi passphrase must be 8..63 characters."
}

if (-not $ValidateOnly -and -not $SkipElevation -and -not (Test-IsAdmin)) {
  Write-Host "Requesting administrator rights for hotspot and firewall setup..."
  Invoke-ElevatedSelf
}

$offlineAdapters = @(Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | Where-Object InterfaceDescription -like "*Wi-Fi Direct*")
if ($offlineAdapters.Count -eq 0) {
  throw "This computer has no Wi-Fi Direct virtual adapter."
}

if ($ValidateOnly) {
  [pscustomobject]@{
    Mode = "Offline Wi-Fi Direct Legacy"
    Capability = "Available"
    PlannedSsid = $Ssid
    Gateway = $Gateway
    Port = $Port
  } | Format-List
  exit 0
}

$tmp = Join-Path $Root "tmp"
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
$readyPath = Join-Path $tmp "keychain_fair_hotspot.ready"
$pidPath = Join-Path $tmp "keychain_fair_hotspot.pid"
$hostLog = Join-Path $tmp "keychain_fair_hotspot_host.log"
$hostErrorLog = Join-Path $tmp "keychain_fair_hotspot_host.err.log"
$upstreamStatePath = Join-Path $tmp "keychain_fair_upstream_wifi.profile"
$stopPath = Join-Path $tmp "keychain_fair_hotspot.stop"

if (Test-Path -LiteralPath $pidPath) {
  $existingPid = Get-Content -LiteralPath $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($existingPid -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
    Write-Host "Restarting offline hotspot advertising..."
    Set-Content -LiteralPath $stopPath -Value "stop"
    Wait-Process -Id $existingPid -Timeout 8 -ErrorAction SilentlyContinue
    if (Get-Process -Id $existingPid -ErrorAction SilentlyContinue) {
      Stop-Process -Id $existingPid -Force -ErrorAction SilentlyContinue
    }
  }
}

$directAdapters = @(Get-NetAdapter -IncludeHidden | Where-Object InterfaceDescription -like "*Wi-Fi Direct*")
$directAdapterNeedsRecovery = $directAdapters.Count -lt 2 -or @($directAdapters | Where-Object Status -eq "Disabled").Count -gt 0
if ((Get-NetIPAddress -AddressFamily IPv4 -IPAddress $Gateway -ErrorAction SilentlyContinue) -or $directAdapterNeedsRecovery) {
  Write-Host "Resetting physical Wi-Fi adapter to clear stale Wi-Fi Direct state..."
  $physicalWifi = Get-NetAdapter -Physical |
    Where-Object InterfaceDescription -match "Wi-?Fi|Wireless|802\.11" |
    Select-Object -First 1
  if (-not $physicalWifi) {
    throw "Physical Wi-Fi adapter was not found."
  }
  $physicalWifi | Disable-NetAdapter -Confirm:$false
  Start-Sleep -Seconds 2
  $physicalWifi | Enable-NetAdapter -Confirm:$false
  Start-Sleep -Seconds 4
  Get-NetAdapter -IncludeHidden |
    Where-Object { $_.InterfaceDescription -like "*Wi-Fi Direct*" -and $_.Status -eq "Disabled" } |
    Enable-NetAdapter -Confirm:$false -ErrorAction SilentlyContinue

  $directDevices = @(Get-PnpDevice -Class Net | Where-Object FriendlyName -like "*Wi-Fi Direct*")
  foreach ($device in $directDevices) {
    if ($device.Status -ne "OK") {
      Enable-PnpDevice -InstanceId $device.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
      & pnputil.exe /restart-device "$($device.InstanceId)" | Out-Null
    }
  }
  & pnputil.exe /scan-devices | Out-Null
  Start-Sleep -Seconds 3
}

Remove-Item -LiteralPath $readyPath, $pidPath, $stopPath -Force -ErrorAction SilentlyContinue
$hostScript = Join-Path $PSScriptRoot "Run-KeychainOfflineAccessPoint.ps1"
$hostArgs = @(
  "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$hostScript`"",
  "-Ssid", "`"$Ssid`"", "-Passphrase", "`"$Passphrase`"", "-Gateway", "`"$Gateway`"",
  "-ReadyPath", "`"$readyPath`"", "-PidPath", "`"$pidPath`"", "-StopPath", "`"$stopPath`""
)
$hostProcess = Start-Process -FilePath "powershell.exe" -ArgumentList $hostArgs -WindowStyle Hidden `
  -RedirectStandardOutput $hostLog -RedirectStandardError $hostErrorLog -PassThru

for ($i = 0; $i -lt 60; $i++) {
  if (Test-Path -LiteralPath $readyPath) { break }
  if ($hostProcess.HasExited) {
    $details = Get-Content -LiteralPath $hostErrorLog -Raw -ErrorAction SilentlyContinue
    throw "Offline hotspot process exited during startup. $details"
  }
  Start-Sleep -Milliseconds 250
}

if (-not (Test-Path -LiteralPath $readyPath)) {
  Stop-Process -Id $hostProcess.Id -Force -ErrorAction SilentlyContinue
  throw "Offline hotspot did not become ready within 15 seconds."
}

Ensure-FirewallRule $Port $Gateway
Disconnect-UpstreamWifi $upstreamStatePath
Write-Host "Offline hotspot ready: SSID=$Ssid gateway=$Gateway port=$Port PID=$($hostProcess.Id)"
exit 0

Initialize-WinRtAsync

$networkInfoType = [Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime]
$managerType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
$configType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringAccessPointConfiguration,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
$operationResultType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]

$profile = $networkInfoType::GetInternetConnectionProfile()
if (-not $profile -and $ValidateOnly) {
  throw "Wi-Fi is disconnected. A normal start would connect saved profile '$UpstreamProfile'."
}
if (-not $profile) {
  $profile = Get-OrConnectUpstreamProfile $networkInfoType $UpstreamProfile
}

$manager = $managerType::CreateFromConnectionProfile($profile)
$capability = $managerType::GetTetheringCapabilityFromConnectionProfile($profile)
$currentConfig = $manager.GetCurrentAccessPointConfiguration()

if ($ValidateOnly) {
  [pscustomobject]@{
    ProfileName = $profile.ProfileName
    PreferredUpstreamProfile = $UpstreamProfile
    Capability = $capability
    OperationalState = $manager.TetheringOperationalState
    CurrentSsid = $currentConfig.Ssid
    PlannedSsid = $Ssid
    Gateway = $Gateway
    Port = $Port
  } | Format-List
  exit 0
}

if ("$capability" -ne "Enabled") {
  throw "Windows reports Mobile Hotspot capability as '$capability'."
}

if ($currentConfig.Ssid -ne $Ssid -or $currentConfig.Passphrase -ne $Passphrase) {
  Write-Host "Configuring Mobile Hotspot SSID '$Ssid'..."
  $newConfig = [Activator]::CreateInstance($configType)
  $newConfig.Ssid = $Ssid
  $newConfig.Passphrase = $Passphrase
  Wait-IAsyncAction ($manager.ConfigureAccessPointAsync($newConfig))
}

if ("$($manager.TetheringOperationalState)" -eq "On") {
  Write-Host "Mobile Hotspot is already on."
} else {
  Start-HotspotWithRetry $manager $operationResultType
}

try {
  Wait-IAsyncAction ($manager.DisableNoConnectionsTimeoutAsync())
} catch {
  Write-Warning "Could not disable hotspot idle timeout: $($_.Exception.Message)"
}

Ensure-FirewallRule $Port $Gateway
Wait-GatewayAddress $Gateway | Out-Null
Write-Host "Hotspot ready: SSID=$Ssid gateway=$Gateway port=$Port"
