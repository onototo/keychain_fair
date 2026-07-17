[CmdletBinding()]
param(
  [int]$Port = 8120
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-UsableIPv4 {
  param([string]$Address)

  return $Address -and
    $Address -notlike "127.*" -and
    $Address -notlike "169.254.*" -and
    $Address -ne "0.0.0.0"
}

function Read-DotenvValue {
  param(
    [string]$Path,
    [string]$Key
  )

  if (-not (Test-Path -LiteralPath $Path)) { return "" }
  foreach ($rawLine in Get-Content -LiteralPath $Path) {
    $line = $rawLine.Trim()
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { continue }
    $parts = $line.Split("=", 2)
    if ($parts[0].Trim() -ne $Key) { continue }
    return $parts[1].Trim().Trim('"').Trim("'")
  }
  return ""
}

function Get-ConfiguredHotspotGateway {
  $dotenvPath = Join-Path (Get-Location) ".env"
  $ssid = if ($env:KEYCHAIN_WIFI_SSID) { $env:KEYCHAIN_WIFI_SSID } else { Read-DotenvValue $dotenvPath "KEYCHAIN_WIFI_SSID" }
  $gateway = if ($env:KEYCHAIN_HOTSPOT_GATEWAY) { $env:KEYCHAIN_HOTSPOT_GATEWAY } else { Read-DotenvValue $dotenvPath "KEYCHAIN_HOTSPOT_GATEWAY" }

  if ($ssid -and $gateway -and (Test-UsableIPv4 $gateway)) {
    return $gateway
  }
  return ""
}

function Get-PreferredIPv4 {
  $hotspotGateway = Get-ConfiguredHotspotGateway
  if ($hotspotGateway) {
    return $hotspotGateway
  }

  try {
    $socket = [System.Net.Sockets.Socket]::new(
      [System.Net.Sockets.AddressFamily]::InterNetwork,
      [System.Net.Sockets.SocketType]::Dgram,
      [System.Net.Sockets.ProtocolType]::Udp
    )
    try {
      $socket.Connect("8.8.8.8", 80)
      $address = $socket.LocalEndPoint.Address.ToString()
      if (Test-UsableIPv4 $address) {
        return $address
      }
    } finally {
      $socket.Dispose()
    }
  } catch {
    # No routed network is available; fall back to localhost below.
  }

  $configs = @(Get-NetIPConfiguration -ErrorAction SilentlyContinue |
    Where-Object { $_.NetAdapter.Status -eq "Up" })

  foreach ($config in @($configs | Where-Object { $_.IPv4DefaultGateway }) + $configs) {
    foreach ($address in @($config.IPv4Address)) {
      if (Test-UsableIPv4 $address.IPAddress) {
        return $address.IPAddress
      }
    }
  }

  return "127.0.0.1"
}

$address = Get-PreferredIPv4
"http://$address`:$Port"
