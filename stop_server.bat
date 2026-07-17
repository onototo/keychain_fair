@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root = (Resolve-Path '.').Path;" ^
  "$pidPath = Join-Path $root 'tmp\keychain_fair_server.pid';" ^
  "$targetIds = @();" ^
  "if (Test-Path $pidPath) { $pidValue = Get-Content $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1; if ($pidValue -and $pidValue -match '^\d+$') { $pidProcess = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $pidValue) -ErrorAction SilentlyContinue; if ($pidProcess -and $pidProcess.Name -like 'python*' -and $pidProcess.CommandLine -like '*uvicorn*keychain_fair.main:app*') { $targetIds += [int]$pidValue } } }" ^
  "$uvicorn = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*uvicorn*keychain_fair.main:app*' };" ^
  "foreach ($item in $uvicorn) { $targetIds += [int]$item.ProcessId }" ^
  "$targetIds = @($targetIds | Sort-Object -Unique);" ^
  "if ($targetIds.Count -eq 0) { Write-Host 'Server is not running.'; if (Test-Path $pidPath) { Remove-Item $pidPath -Force }; exit 0 }" ^
  "foreach ($targetId in $targetIds) { $process = Get-Process -Id $targetId -ErrorAction SilentlyContinue; if ($process) { Stop-Process -Id $targetId -Force; Write-Host ('Stopped PID ' + $targetId) } }" ^
  "for ($i = 0; $i -lt 20; $i++) { $remaining = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*uvicorn*keychain_fair.main:app*' } | Select-Object -First 1; if (-not $remaining) { break }; Start-Sleep -Milliseconds 250 }" ^
  "$remaining = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*uvicorn*keychain_fair.main:app*' } | Select-Object -First 1;" ^
  "if ($remaining) { Write-Error ('Local server is still running as PID ' + $remaining.ProcessId); exit 1 }" ^
  "if (Test-Path $pidPath) { Remove-Item $pidPath -Force }" ^
  "Write-Host 'Server stopped.'"

set "SERVER_STOP_STATUS=%ERRORLEVEL%"
if not "%SERVER_STOP_STATUS%"=="0" exit /b %SERVER_STOP_STATUS%

exit /b 0
