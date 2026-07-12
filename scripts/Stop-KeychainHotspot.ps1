[CmdletBinding()]
param(
  [string]$Ssid,
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
    "-SkipElevation"
  )
  $process = Start-Process -FilePath "powershell.exe" -ArgumentList $argumentList -Verb RunAs -Wait -PassThru
  exit $process.ExitCode
}

function Initialize-WinRtAsync {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime

  $script:AsTaskOperation = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
      $_.Name -eq "AsTask" -and
      $_.GetParameters().Count -eq 1 -and
      $_.GetParameters()[0].ParameterType.IsGenericType -and
      $_.GetParameters()[0].ParameterType.GetGenericTypeDefinition().FullName -eq 'Windows.Foundation.IAsyncOperation`1'
    } |
    Select-Object -First 1

  if (-not $script:AsTaskOperation) {
    throw "Windows Runtime async helpers are unavailable."
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

function Get-CandidateManagers {
  param(
    [Type]$NetworkInfoType,
    [Type]$ManagerType
  )

  $profiles = @()
  $internetProfile = $NetworkInfoType::GetInternetConnectionProfile()
  if ($internetProfile) {
    $profiles += $internetProfile
  }

  foreach ($profile in $NetworkInfoType::GetConnectionProfiles()) {
    $profiles += $profile
  }

  $managers = @()
  $seenManagers = @{}
  foreach ($profile in $profiles) {
    try {
      $manager = $ManagerType::CreateFromConnectionProfile($profile)
      $config = $manager.GetCurrentAccessPointConfiguration()
      $key = "$($manager.TetheringOperationalState)|$($config.Ssid)"
      if (-not $seenManagers.ContainsKey($key)) {
        $seenManagers[$key] = $true
        $managers += $manager
      }
    } catch {
      continue
    }
  }

  return $managers
}

Import-DotEnv (Join-Path $Root ".env")
if (-not $Ssid) { $Ssid = $env:KEYCHAIN_WIFI_SSID }
if (-not $Ssid) { $Ssid = "KeychainFair" }

if (-not $ValidateOnly -and -not $SkipElevation -and -not (Test-IsAdmin)) {
  Write-Host "Requesting administrator rights to stop Mobile Hotspot..."
  Invoke-ElevatedSelf
}

Initialize-WinRtAsync

$networkInfoType = [Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime]
$managerType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
$operationResultType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]

$managers = @(Get-CandidateManagers $networkInfoType $managerType)
if ($ValidateOnly) {
  foreach ($manager in $managers) {
    $config = $manager.GetCurrentAccessPointConfiguration()
    [pscustomobject]@{
      OperationalState = $manager.TetheringOperationalState
      CurrentSsid = $config.Ssid
      TargetSsid = $Ssid
    } | Format-List
  }
  exit 0
}

foreach ($manager in $managers) {
  $config = $manager.GetCurrentAccessPointConfiguration()
  if ($config.Ssid -ne $Ssid) {
    continue
  }

  if ("$($manager.TetheringOperationalState)" -ne "On") {
    Write-Host "Mobile Hotspot '$Ssid' is already off."
    exit 0
  }

  Write-Host "Stopping Mobile Hotspot '$Ssid'..."
  $result = Wait-IAsyncOperation ($manager.StopTetheringAsync()) $operationResultType
  if ("$($result.Status)" -ne "Success") {
    throw "Mobile Hotspot did not stop: $($result.Status) $($result.AdditionalErrorMessage)"
  }
  Write-Host "Mobile Hotspot '$Ssid' stopped."
  exit 0
}

Write-Host "No Mobile Hotspot with SSID '$Ssid' was found."
