@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Python venv was not found at .venv\Scripts\python.exe
  exit /b 1
)

if not exist "tmp" mkdir "tmp"

".venv\Scripts\python.exe" "scripts\generate_static_qr.py"
if errorlevel 1 exit /b %ERRORLEVEL%

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$root = (Resolve-Path '.').Path;" ^
  "$pidPath = Join-Path $root 'tmp\keychain_fair_online_server.pid';" ^
  "$listener = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1;" ^
  "if ($listener) { $owner = Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -eq $listener.OwningProcess } | Select-Object -First 1; if ($owner -and $owner.CommandLine -like '*uvicorn*keychain_fair.main:app*') { Set-Content -Path $pidPath -Value $listener.OwningProcess; Write-Host ('Server already running on http://127.0.0.1:8080/ PID ' + $listener.OwningProcess); exit 0 }; Write-Error ('Port 8080 is already in use by PID ' + $listener.OwningProcess); exit 1 };" ^
  "$out = Join-Path $root 'tmp\keychain_fair_online_server.out.log';" ^
  "$err = Join-Path $root 'tmp\keychain_fair_online_server.err.log';" ^
  "$python = Join-Path $root '.venv\Scripts\python.exe';" ^
  "$args = @('-m','uvicorn','keychain_fair.main:app','--host','0.0.0.0','--port','8080');" ^
  "$process = Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru;" ^
  "$ownerPid = $null;" ^
  "for ($i = 0; $i -lt 30; $i++) { Start-Sleep -Milliseconds 500; $listener = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($listener) { $ownerPid = [int]$listener.OwningProcess; break } }" ^
  "if (-not $ownerPid) { Write-Error ('Server did not start. See ' + $err); exit 1 }" ^
  "Set-Content -Path $pidPath -Value $ownerPid;" ^
  "Write-Host ('Online server started on http://127.0.0.1:8080/ PID ' + $ownerPid);" ^
  "Write-Host 'Open your HTTPS tunnel to http://127.0.0.1:8080 and then run scripts\Set-TelegramWebhook.ps1.';"

exit /b %ERRORLEVEL%
