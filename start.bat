@echo off
setlocal
cd /d "%~dp0"
if not exist "usersecrets.json" copy /Y "usersecrets.example.json" "usersecrets.json" >nul
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto fail
".venv\Scripts\python.exe" app.py %*
if errorlevel 1 goto fail
exit /b 0
:fail
echo Setup or launch failed. Check Python, the network connection, and the port.
pause
exit /b 1
