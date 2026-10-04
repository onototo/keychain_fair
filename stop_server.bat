@echo off
setlocal
cd /d "%~dp0"

if "%~1"=="" goto menu
if /i "%~1"=="local" goto local_stop
if /i "%~1"=="docker" goto docker_stop

echo Unknown command: %~1
echo.
echo Usage:
echo   stop_server.bat
echo   stop_server.bat local
echo   stop_server.bat docker
exit /b 1

:menu
echo.
echo Stop Keychain Fair server
echo   1 - local server
echo   2 - Docker server
echo   3 - exit
choice /c 123 /n /m "Enter 1-3: "
if errorlevel 3 exit /b 0
if errorlevel 2 goto docker_stop
goto local_stop

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

:local_stop
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root = (Resolve-Path '.').Path;" ^
  "$pidPath = Join-Path $root 'tmp\keychain_fair_server.pid';" ^
  "$targetIds = @();" ^
  "if (Test-Path $pidPath) { $pidValue = Get-Content $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1; if ($pidValue -and $pidValue -match '^\d+$') { $pidProcess = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $pidValue) -ErrorAction SilentlyContinue; if ($pidProcess -and $pidProcess.Name -like 'python*' -and $pidProcess.CommandLine -like '*uvicorn*keychain_fair.main:create_app*') { $targetIds += [int]$pidValue } } }" ^
  "$uvicorn = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*uvicorn*keychain_fair.main:create_app*' };" ^
  "foreach ($item in $uvicorn) { $targetIds += [int]$item.ProcessId }" ^
  "$targetIds = @($targetIds | Sort-Object -Unique);" ^
  "if ($targetIds.Count -eq 0) { Write-Host 'Server is not running.'; if (Test-Path $pidPath) { Remove-Item $pidPath -Force }; exit 0 }" ^
  "foreach ($targetId in $targetIds) { $process = Get-Process -Id $targetId -ErrorAction SilentlyContinue; if ($process) { Stop-Process -Id $targetId -Force; Write-Host ('Stopped PID ' + $targetId) } }" ^
  "for ($i = 0; $i -lt 20; $i++) { $remaining = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*uvicorn*keychain_fair.main:create_app*' } | Select-Object -First 1; if (-not $remaining) { break }; Start-Sleep -Milliseconds 250 }" ^
  "$remaining = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*uvicorn*keychain_fair.main:create_app*' } | Select-Object -First 1;" ^
  "if ($remaining) { Write-Error ('Local server is still running as PID ' + $remaining.ProcessId); exit 1 }" ^
  "if (Test-Path $pidPath) { Remove-Item $pidPath -Force }" ^
  "Write-Host 'Server stopped.'"

set "SERVER_STOP_STATUS=%ERRORLEVEL%"
if not "%SERVER_STOP_STATUS%"=="0" exit /b %SERVER_STOP_STATUS%

exit /b 0

:docker_stop
call :local_stop
if errorlevel 1 exit /b %ERRORLEVEL%

call :find_docker_cli
if errorlevel 1 exit /b %ERRORLEVEL%

docker compose down
if errorlevel 1 exit /b %ERRORLEVEL%

echo Docker server stopped.
exit /b 0
