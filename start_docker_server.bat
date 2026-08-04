@echo off
setlocal
cd /d "%~dp0"

set "PUBLIC_URL="
for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\Get-KeychainLanUrl.ps1" -Port 8120`) do set "PUBLIC_URL=%%I"
if not defined PUBLIC_URL set "PUBLIC_URL=http://127.0.0.1:8120"

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

set "DOCKER_PATH="
for /f "delims=" %%I in ('where docker 2^>nul') do (
  if not defined DOCKER_PATH set "DOCKER_PATH=%%I"
)

if not defined DOCKER_PATH (
  echo Docker CLI was not found in PATH.
  echo Install Docker Desktop or open a new terminal after installation.
  exit /b 1
)

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

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\generate_static_qr.py"
  if errorlevel 1 exit /b %ERRORLEVEL%
)

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

exit /b 0
