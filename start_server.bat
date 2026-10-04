@echo off
setlocal
cd /d "%~dp0"

if "%~1"=="" goto menu
if /i "%~1"=="local" goto local_start
if /i "%~1"=="docker" goto docker_start

echo Unknown command: %~1
echo.
echo Usage:
echo   start_server.bat
echo   start_server.bat local
echo   start_server.bat docker
exit /b 1

:menu
echo.
echo Start shop server
echo   1 - local API
echo   2 - Docker API and Telegram bot
echo   3 - exit
choice /c 123 /n /m "Enter 1-3: "
if errorlevel 3 exit /b 0
if errorlevel 2 goto docker_start
goto local_start

:find_docker_cli
set "DOCKER_PATH="
for /f "delims=" %%I in ('where docker 2^>nul') do (
  if not defined DOCKER_PATH set "DOCKER_PATH=%%I"
)
if not defined DOCKER_PATH (
  echo Docker CLI was not found in PATH.
  exit /b 1
)
exit /b 0

:check_docker
call :find_docker_cli
if errorlevel 1 exit /b %ERRORLEVEL%
docker info >nul 2>nul
if errorlevel 1 (
  echo Docker daemon is not available.
  exit /b 1
)
exit /b 0

:local_start
if not exist ".venv\Scripts\python.exe" (
  echo Python venv was not found at .venv\Scripts\python.exe
  exit /b 1
)
if not exist "tmp" mkdir "tmp"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$root = (Resolve-Path '.').Path;" ^
  "$pidPath = Join-Path $root 'tmp\keychain_fair_server.pid';" ^
  "$listener = Get-NetTCPConnection -LocalPort 8120 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1;" ^
  "if ($listener) { $owner = Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -eq $listener.OwningProcess } | Select-Object -First 1; if ($owner -and $owner.CommandLine -like '*uvicorn*keychain_fair.main:create_app*') { Set-Content -Path $pidPath -Value $listener.OwningProcess; Write-Host ('API already running on http://127.0.0.1:8120/ PID ' + $listener.OwningProcess); exit 0 }; Write-Error ('Port 8120 is already in use by PID ' + $listener.OwningProcess); exit 1 };" ^
  "$out = Join-Path $root 'tmp\keychain_fair_server.out.log';" ^
  "$err = Join-Path $root 'tmp\keychain_fair_server.err.log';" ^
  "$python = Join-Path $root '.venv\Scripts\python.exe';" ^
  "$uvicornArgs = @('-m','uvicorn','keychain_fair.main:create_app','--factory','--host','0.0.0.0','--port','8120');" ^
  "$process = Start-Process -FilePath $python -ArgumentList $uvicornArgs -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru;" ^
  "$ownerPid = $null;" ^
  "for ($i = 0; $i -lt 30; $i++) { Start-Sleep -Milliseconds 500; $listener = Get-NetTCPConnection -LocalPort 8120 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($listener) { $ownerPid = [int]$listener.OwningProcess; break } }" ^
  "if (-not $ownerPid) { Write-Error ('API did not start. See ' + $err); exit 1 }" ^
  "Set-Content -Path $pidPath -Value $ownerPid;" ^
  "Write-Host ('API started on http://127.0.0.1:8120/ PID ' + $ownerPid);"
exit /b %ERRORLEVEL%

:docker_start
call :check_docker
if errorlevel 1 exit /b %ERRORLEVEL%
docker compose up --build -d api telegram-bot
if errorlevel 1 exit /b %ERRORLEVEL%
docker compose ps
echo Docker API and Telegram bot started.
exit /b 0
