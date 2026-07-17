@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Python venv was not found at .venv\Scripts\python.exe
  exit /b 1
)

if not exist "tmp" mkdir "tmp"

set "SERVER_ALREADY_RUNNING=0"
set "PUBLIC_URL="
if not defined OCTOPRINT_LOCAL_BASE_URL set "OCTOPRINT_LOCAL_BASE_URL=http://127.0.0.1:5000"
set "OCTOPRINT_BASE_URL=%OCTOPRINT_LOCAL_BASE_URL%"

for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\Get-KeychainLanUrl.ps1" -Port 8120`) do set "PUBLIC_URL=%%I"
if not defined PUBLIC_URL set "PUBLIC_URL=http://127.0.0.1:8120"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$root = (Resolve-Path '.').Path;" ^
  "$pidPath = Join-Path $root 'tmp\keychain_fair_server.pid';" ^
  "$listener = Get-NetTCPConnection -LocalPort 8120 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1;" ^
  "if ($listener) { $owner = Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -eq $listener.OwningProcess } | Select-Object -First 1; if ($owner -and $owner.CommandLine -like '*uvicorn*keychain_fair.main:app*') { Set-Content -Path $pidPath -Value $listener.OwningProcess; Write-Host ('Server already running on ' + $env:PUBLIC_URL + '/ PID ' + $listener.OwningProcess); exit 2 }; Write-Error ('Port 8120 is already in use by PID ' + $listener.OwningProcess); exit 1 }; exit 0"

set "PORT_STATUS=%ERRORLEVEL%"
if "%PORT_STATUS%"=="1" exit /b 1
if not "%PORT_STATUS%"=="0" if not "%PORT_STATUS%"=="2" exit /b %PORT_STATUS%
if "%PORT_STATUS%"=="2" set "SERVER_ALREADY_RUNNING=1"

".venv\Scripts\python.exe" "scripts\generate_static_qr.py"
if errorlevel 1 exit /b %ERRORLEVEL%

if "%SERVER_ALREADY_RUNNING%"=="1" (
  echo Site:  %PUBLIC_URL%/
  echo Admin: %PUBLIC_URL%/admin
  exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$root = (Resolve-Path '.').Path;" ^
  "$pidPath = Join-Path $root 'tmp\keychain_fair_server.pid';" ^
  "$out = Join-Path $root 'tmp\keychain_fair_server.out.log';" ^
  "$err = Join-Path $root 'tmp\keychain_fair_server.err.log';" ^
  "$python = Join-Path $root '.venv\Scripts\python.exe';" ^
  "$uvicornArgs = @('-m','uvicorn','keychain_fair.main:app','--host','0.0.0.0','--port','8120');" ^
  "$process = Start-Process -FilePath $python -ArgumentList $uvicornArgs -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru;" ^
  "$ownerPid = $null;" ^
  "for ($i = 0; $i -lt 30; $i++) { Start-Sleep -Milliseconds 500; $listener = Get-NetTCPConnection -LocalPort 8120 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($listener) { $ownerPid = [int]$listener.OwningProcess; break } }" ^
  "if (-not $ownerPid) { Write-Error ('Server did not start. See ' + $err); exit 1 }" ^
  "Set-Content -Path $pidPath -Value $ownerPid;" ^
  "Write-Host ('Server started on ' + $env:PUBLIC_URL + '/ PID ' + $ownerPid);" ^
  "Write-Host ('Site:  ' + $env:PUBLIC_URL + '/');" ^
  "Write-Host ('Admin: ' + $env:PUBLIC_URL + '/admin');"

exit /b %ERRORLEVEL%
