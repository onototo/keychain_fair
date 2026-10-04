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
call :ensure_docker
if errorlevel 1 exit /b 1

echo Starting API and Telegram bot...
docker compose up --build -d api telegram-bot
if errorlevel 1 (
  echo Docker Compose failed.
  exit /b 1
)

echo Waiting until the API and the bot are running...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$deadline = (Get-Date).AddMinutes(3);" ^
  "do {" ^
  "  $running = @(docker compose ps --status running --services 2>$null);" ^
  "  $apiUp = $running -contains 'api';" ^
  "  $botUp = $running -contains 'telegram-bot';" ^
  "  $healthy = $false;" ^
  "  try { $response = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8120/api/health -TimeoutSec 2; if ($response.StatusCode -eq 200) { $healthy = $true } } catch {}" ^
  "  if ($apiUp -and $botUp -and $healthy) { exit 0 };" ^
  "  Start-Sleep -Seconds 2" ^
  "} while ((Get-Date) -lt $deadline);" ^
  "docker compose ps;" ^
  "docker compose logs --tail 40 telegram-bot;" ^
  "exit 1"
if errorlevel 1 (
  echo The server did not become ready. See the log above.
  exit /b 1
)

docker compose ps
echo API and Telegram bot are running.
echo Health: http://127.0.0.1:8120/api/health
exit /b 0

:ensure_docker
set "DOCKER_DESKTOP=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
if exist "%ProgramFiles%\Docker\Docker\resources\bin\docker.exe" (
  set "PATH=%ProgramFiles%\Docker\Docker\resources\bin;%PATH%"
)
where docker >nul 2>nul
if errorlevel 1 (
  echo Docker is not installed.
  exit /b 1
)

docker info >nul 2>nul
if not errorlevel 1 exit /b 0

if not exist "%DOCKER_DESKTOP%" (
  echo Docker is installed, but Docker Desktop was not found.
  exit /b 1
)

echo Starting Docker Desktop...
start "" "%DOCKER_DESKTOP%"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$deadline = (Get-Date).AddMinutes(4);" ^
  "do { docker info *> $null; if ($LASTEXITCODE -eq 0) { exit 0 }; Start-Sleep -Seconds 3 } while ((Get-Date) -lt $deadline);" ^
  "exit 1"
if errorlevel 1 (
  echo Docker Desktop did not become ready.
  exit /b 1
)
exit /b 0
