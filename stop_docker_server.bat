@echo off
setlocal
cd /d "%~dp0"

set "DOCKER_PATH="
for /f "delims=" %%I in ('where docker 2^>nul') do (
  if not defined DOCKER_PATH set "DOCKER_PATH=%%I"
)

if not defined DOCKER_PATH (
  echo Docker CLI was not found in PATH.
  exit /b 1
)

docker compose down
if errorlevel 1 exit /b %ERRORLEVEL%

echo Docker server stopped.
exit /b 0
