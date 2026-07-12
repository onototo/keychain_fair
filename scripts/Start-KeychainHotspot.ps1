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
  $process = Start-Process -FilePath "powershell.exe" -ArgumentList $argumentList -Verb RunAs -Wait -PassThru
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

Initialize-WinRtAsync

$networkInfoType = [Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime]
$managerType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
$configType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringAccessPointConfiguration,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
$operationResultType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]

$profile = $networkInfoType::GetInternetConnectionProfile()
if (-not $profile) {
  throw "No internet connection profile is available to create Mobile Hotspot. Connect the laptop to Wi-Fi first, then run start_server.bat."
}

$manager = $managerType::CreateFromConnectionProfile($profile)
$capability = $managerType::GetTetheringCapabilityFromConnectionProfile($profile)
$currentConfig = $manager.GetCurrentAccessPointConfiguration()

if ($ValidateOnly) {
  [pscustomobject]@{
    ProfileName = $profile.ProfileName
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
