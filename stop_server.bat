@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
call :main
set "EXIT_CODE=%ERRORLEVEL%"
echo.
pause
exit /b %EXIT_CODE%

:main
if exist "%ProgramFiles%\Docker\Docker\resources\bin\docker.exe" (
  set "PATH=%ProgramFiles%\Docker\Docker\resources\bin;%PATH%"
)
where docker >nul 2>nul
if errorlevel 1 (
  echo Docker is not installed.
  exit /b 1
)

docker info >nul 2>nul
if errorlevel 1 (
  echo Docker is already stopped.
  exit /b 0
)

echo Stopping API and Telegram bot...
docker compose down
if errorlevel 1 (
  echo Docker Compose did not stop the containers.
  exit /b 1
)

set "DOCKER_CLI=%ProgramFiles%\Docker\Docker\DockerCli.exe"
if exist "%DOCKER_CLI%" (
  echo Stopping Docker Desktop...
  "%DOCKER_CLI%" -Shutdown
)

echo API, Telegram bot, and Docker are stopped.
exit /b 0
