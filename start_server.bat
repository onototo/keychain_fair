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
echo Start Keychain Fair server
echo   1 - local server
echo   2 - Docker server
echo   3 - exit
choice /c 123 /n /m "Enter 1-3: "
if errorlevel 3 exit /b 0
if errorlevel 2 goto docker_start
goto local_start

:choose_blank_size
echo.
echo Select blank size for this server start:
echo   1 - compact  (56x24 mm, up to 18 blanks)
echo   2 - standard (64x30 mm, up to 10 blanks)
choice /c 12 /n /m "Enter 1 or 2: "
if errorlevel 2 (
  set "BLANK_SIZE_ID=standard"
  set "BLANK_SIZE_LABEL=standard"
) else (
  set "BLANK_SIZE_ID=compact"
  set "BLANK_SIZE_LABEL=compact"
)
echo Blank size: %BLANK_SIZE_LABEL%
echo.
exit /b 0

:set_public_url
set "PUBLIC_URL="
for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\Get-KeychainLanUrl.ps1" -Port 8120`) do set "PUBLIC_URL=%%I"
if not defined PUBLIC_URL set "PUBLIC_URL=http://127.0.0.1:8120"
exit /b 0

:find_docker_cli
set "DOCKER_PATH="
for /f "delims=" %%I in ('where docker 2^>nul') do (
  if not defined DOCKER_PATH set "DOCKER_PATH=%%I"
)

if not defined DOCKER_PATH (
  echo Docker CLI was not found in PATH.
  echo Install Docker Desktop or open a new terminal after installation.
  exit /b 1
)
exit /b 0

:check_docker
call :find_docker_cli
if errorlevel 1 exit /b %ERRORLEVEL%

docker info >nul 2>nul
if errorlevel 1 (
  if exist "C:\Program Files\Docker\Docker\Docker Desktop.exe" (
    echo Starting Docker Desktop...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'C:\Program Files\Docker\Docker\Docker Desktop.exe' -WindowStyle Hidden"
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$ErrorActionPreference = 'SilentlyContinue';" ^
      "$deadline = (Get-Date).AddMinutes(4);" ^
      "do { docker info *> $null; if ($LASTEXITCODE -eq 0) { exit 0 }; Start-Sleep -Seconds 5 } while ((Get-Date) -lt $deadline);" ^
      "exit 1"
    if errorlevel 1 (
      echo Docker Desktop did not become ready in time.
      exit /b 1
    )
  ) else (
    echo Docker daemon is not available.
    exit /b 1
  )
)
exit /b 0

:generate_static_qr_required
".venv\Scripts\python.exe" "scripts\generate_static_qr.py"
if errorlevel 1 exit /b %ERRORLEVEL%
exit /b 0

:generate_static_qr_optional
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\generate_static_qr.py"
  if errorlevel 1 exit /b %ERRORLEVEL%
)
exit /b 0

:local_start
if not exist ".venv\Scripts\python.exe" (
  echo Python venv was not found at .venv\Scripts\python.exe
  exit /b 1
)

if not exist "tmp" mkdir "tmp"

set "SERVER_ALREADY_RUNNING=0"
if not defined OCTOPRINT_LOCAL_BASE_URL set "OCTOPRINT_LOCAL_BASE_URL=http://127.0.0.1:5000"
set "OCTOPRINT_BASE_URL=%OCTOPRINT_LOCAL_BASE_URL%"

call :choose_blank_size
if errorlevel 1 exit /b %ERRORLEVEL%

call :set_public_url
if errorlevel 1 exit /b %ERRORLEVEL%

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

call :generate_static_qr_required
if errorlevel 1 exit /b %ERRORLEVEL%

if "%SERVER_ALREADY_RUNNING%"=="1" (
  echo Server is already running; selected blank size was not applied.
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

:docker_start
call :set_public_url
if errorlevel 1 exit /b %ERRORLEVEL%

call :choose_blank_size
if errorlevel 1 exit /b %ERRORLEVEL%

call :check_docker
if errorlevel 1 exit /b %ERRORLEVEL%

call :generate_static_qr_optional
if errorlevel 1 exit /b %ERRORLEVEL%

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root = (Resolve-Path '.').Path;" ^
  "$pidPath = Join-Path $root 'tmp\keychain_fair_server.pid';" ^
  "$uvicorn = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*uvicorn*keychain_fair.main:app*' };" ^
  "$targetIds = @($uvicorn | ForEach-Object { [int]$_.ProcessId } | Sort-Object -Unique);" ^
  "foreach ($targetId in $targetIds) { $process = Get-Process -Id $targetId -ErrorAction SilentlyContinue; if (-not $process) { continue }; try { Stop-Process -Id $targetId -Force -ErrorAction Stop; Write-Host ('Stopped local server PID ' + $targetId) } catch { if (Get-Process -Id $targetId -ErrorAction SilentlyContinue) { throw } } };" ^
  "if (Test-Path $pidPath) { Remove-Item $pidPath -Force }"
if errorlevel 1 exit /b %ERRORLEVEL%

docker compose up --build -d postgres api telegram-bot
if errorlevel 1 exit /b %ERRORLEVEL%

docker compose ps
echo.
echo Docker server started.
echo Site:  %PUBLIC_URL%/
echo Admin: %PUBLIC_URL%/admin

exit /b %ERRORLEVEL%
