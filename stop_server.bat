@echo off
setlocal
cd /d "%~dp0"

where docker >nul 2>nul
if errorlevel 1 (
  echo Docker CLI was not found in PATH.
  exit /b 1
)

docker compose down
if errorlevel 1 exit /b %ERRORLEVEL%

echo Docker API and Telegram bot stopped.
exit /b 0
