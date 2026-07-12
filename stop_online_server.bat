@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root = (Resolve-Path '.').Path;" ^
  "$pidPath = Join-Path $root 'tmp\keychain_fair_online_server.pid';" ^
  "$targetIds = @();" ^
  "if (Test-Path $pidPath) { $pidValue = Get-Content $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1; if ($pidValue) { $targetIds += [int]$pidValue } }" ^
  "$uvicorn = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*uvicorn*keychain_fair.main:app*' };" ^
  "foreach ($item in $uvicorn) { $targetIds += [int]$item.ProcessId }" ^
  "$targetIds = @($targetIds | Sort-Object -Unique);" ^
  "if ($targetIds.Count -eq 0) { Write-Host 'Online server is not running.'; if (Test-Path $pidPath) { Remove-Item $pidPath -Force }; exit 0 }" ^
  "foreach ($targetId in $targetIds) { $process = Get-Process -Id $targetId -ErrorAction SilentlyContinue; if ($process) { Stop-Process -Id $targetId -Force; Write-Host ('Stopped PID ' + $targetId) } }" ^
  "for ($i = 0; $i -lt 20; $i++) { $listener = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if (-not $listener) { break }; Start-Sleep -Milliseconds 250 }" ^
  "$listener = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1;" ^
  "if ($listener) { Write-Error ('Port 8080 is still in use by PID ' + $listener.OwningProcess); exit 1 }" ^
  "if (Test-Path $pidPath) { Remove-Item $pidPath -Force }" ^
  "Write-Host 'Online server stopped.'"

exit /b %ERRORLEVEL%
