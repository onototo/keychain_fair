param(
  [string]$PublicUrl = "",
  [string]$BotToken = "",
  [string]$WebhookSecret = ""
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dotenvPath = Join-Path $root ".env"

function Get-DotEnvValue([string]$Name) {
  if (-not (Test-Path $dotenvPath)) {
    return ""
  }
  $line = Get-Content $dotenvPath -Encoding UTF8 | Where-Object { $_ -match "^\s*$Name\s*=" } | Select-Object -First 1
  if (-not $line) {
    return ""
  }
  return (($line -split "=", 2)[1]).Trim().Trim('"').Trim("'")
}

if (-not $PublicUrl) {
  $PublicUrl = $env:PUBLIC_URL
}
if (-not $PublicUrl) {
  $PublicUrl = Get-DotEnvValue "PUBLIC_URL"
}
if (-not $BotToken) {
  $BotToken = $env:TELEGRAM_BOT_TOKEN
}
if (-not $BotToken) {
  $BotToken = Get-DotEnvValue "TELEGRAM_BOT_TOKEN"
}
if (-not $WebhookSecret) {
  $WebhookSecret = $env:TELEGRAM_WEBHOOK_SECRET
}
if (-not $WebhookSecret) {
  $WebhookSecret = Get-DotEnvValue "TELEGRAM_WEBHOOK_SECRET"
}

if (-not $PublicUrl) {
  throw "PUBLIC_URL is required. Use the HTTPS tunnel URL."
}
if (-not $BotToken) {
  throw "TELEGRAM_BOT_TOKEN is required."
}
if (-not $WebhookSecret) {
  throw "TELEGRAM_WEBHOOK_SECRET is required."
}

$webhookUrl = $PublicUrl.TrimEnd("/") + "/api/telegram/webhook"
$apiUrl = "https://api.telegram.org/bot$BotToken/setWebhook"
$body = @{
  url = $webhookUrl
  secret_token = $WebhookSecret
  drop_pending_updates = "true"
}

$result = Invoke-RestMethod -Uri $apiUrl -Method Post -Body $body
if (-not $result.ok) {
  throw "Telegram rejected webhook setup: $($result | ConvertTo-Json -Compress)"
}

Write-Host "Telegram webhook set to $webhookUrl"
