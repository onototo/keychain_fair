@echo off
setlocal
cd /d "%~dp0"

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

docker compose up --build -d postgres api telegram-bot
if errorlevel 1 exit /b %ERRORLEVEL%

docker compose ps
echo.
echo Docker server started.
echo Site:  http://127.0.0.1:8080/
echo Admin: http://127.0.0.1:8080/admin

exit /b 0
