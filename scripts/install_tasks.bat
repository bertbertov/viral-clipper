@echo off
REM Install scheduled tasks (no admin required, user-level)
REM Run: install_tasks.bat

set PYTHON=C:\Users\A\AppData\Local\Python\pythoncore-3.14-64\python.exe
set WORKER_DIR=C:\Users\A\clips-pipeline\worker

REM Read admin token
for /f "tokens=2 delims==" %%a in ('findstr "ADMIN_TOKEN" "C:\Users\A\clips-pipeline\.tokens"') do set ADMIN_TOKEN=%%a

REM Set as user environment variable so child processes (the discovery script) inherit it
setx CLIPS_ADMIN_TOKEN "%ADMIN_TOKEN%" >nul

REM ---- ClipsWorker: starts at logon, runs forever ----
schtasks /Create /F /SC ONLOGON /TN "ClipsWorker" ^
  /TR "\"%PYTHON%\" \"%WORKER_DIR%\worker.py\"" ^
  /RL LIMITED /IT /DELAY 0001:00 ^
  /RU "%USERNAME%"

REM ---- ClipsDiscovery: daily at 10:00 + at logon ----
schtasks /Create /F /SC DAILY /ST 10:00 /TN "ClipsDiscovery" ^
  /TR "\"%PYTHON%\" \"%WORKER_DIR%\discovery.py\"" ^
  /RL LIMITED /IT ^
  /RU "%USERNAME%"

REM Add a second trigger (logon) to ClipsDiscovery via XML edit
schtasks /Run /TN "ClipsDiscovery" >nul 2>&1

echo.
echo Tasks installed:
schtasks /Query /TN "ClipsWorker" /FO LIST | findstr "TaskName Status"
schtasks /Query /TN "ClipsDiscovery" /FO LIST | findstr "TaskName Status"

echo.
echo NOTE: CLIPS_ADMIN_TOKEN env var was set. May need to log out + back in for new shells to see it.
echo To start now:    schtasks /Run /TN ClipsWorker
echo                  schtasks /Run /TN ClipsDiscovery
echo To remove later: schtasks /Delete /F /TN ClipsWorker
echo                  schtasks /Delete /F /TN ClipsDiscovery
