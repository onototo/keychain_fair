@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Python venv was not found at .venv\Scripts\python.exe
  exit /b 1
)

if not defined OCTOPRINT_CONNECT_BASE_URL set "OCTOPRINT_CONNECT_BASE_URL=http://127.0.0.1:5000"
set "OCTOPRINT_BASE_URL=%OCTOPRINT_CONNECT_BASE_URL%"

echo Connecting printer through OctoPrint at %OCTOPRINT_BASE_URL% ...
".venv\Scripts\python.exe" "scripts\connect_printer.py"
exit /b %ERRORLEVEL%
