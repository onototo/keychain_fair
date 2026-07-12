[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Ssid,
  [Parameter(Mandatory = $true)][string]$Passphrase,
  [Parameter(Mandatory = $true)][string]$Gateway,
  [Parameter(Mandatory = $true)][string]$ReadyPath,
  [Parameter(Mandatory = $true)][string]$PidPath,
  [Parameter(Mandatory = $true)][string]$StopPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$publisher = $null
try {
  Set-Content -LiteralPath $PidPath -Value $PID

  $publisherType = [Windows.Devices.WiFiDirect.WiFiDirectAdvertisementPublisher,Windows.Devices.WiFiDirect,ContentType=WindowsRuntime]
  $publisher = [Activator]::CreateInstance($publisherType)
  $publisher.Advertisement.IsAutonomousGroupOwnerEnabled = $true

  $legacy = $publisher.Advertisement.LegacySettings
  $legacy.IsEnabled = $true
  $legacy.Ssid = $Ssid
  $legacy.Passphrase.Password = $Passphrase

  $publisher.Start()
  for ($i = 0; $i -lt 40; $i++) {
    if ("$($publisher.Status)" -eq "Started") {
      break
    }
    if ("$($publisher.Status)" -eq "Aborted") {
      throw "Wi-Fi Direct publisher was aborted by Windows."
    }
    Start-Sleep -Milliseconds 250
  }

  if ("$($publisher.Status)" -ne "Started") {
    throw "Wi-Fi Direct access point did not start. Status: $($publisher.Status)"
  }

  for ($i = 0; $i -lt 40; $i++) {
    $address = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $Gateway -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($address) {
      break
    }
    Start-Sleep -Milliseconds 250
  }

  if (-not $address) {
    throw "Offline access point started but gateway $Gateway was not assigned."
  }

  Set-Content -LiteralPath $ReadyPath -Value "SSID=$Ssid`nGateway=$Gateway`nPID=$PID"
  while ("$($publisher.Status)" -eq "Started" -and -not (Test-Path -LiteralPath $StopPath)) {
    Start-Sleep -Seconds 2
  }

  if (Test-Path -LiteralPath $StopPath) {
    exit 0
  }

  throw "Offline access point stopped unexpectedly. Status: $($publisher.Status)"
} finally {
  if ($publisher) {
    try { $publisher.Stop() } catch {}
  }
  Remove-Item -LiteralPath $ReadyPath -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $StopPath -Force -ErrorAction SilentlyContinue
}
