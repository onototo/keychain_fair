@echo off
setlocal
cd /d "%~dp0"

where docker >nul 2>nul
if errorlevel 1 (
  echo Docker CLI was not found in PATH.
  exit /b 1
)

docker info >nul 2>nul
if errorlevel 1 (
  echo Docker daemon is not available.
  exit /b 1
)

docker compose up --build -d api telegram-bot
if errorlevel 1 exit /b %ERRORLEVEL%

docker compose ps
echo Docker API and Telegram bot started.
exit /b 0
