@echo off
rem Start the tray icon without a console window.
setlocal
set ROOT=%~dp0..
cd /d "%ROOT%"

set PY=pythonw
where pythonw >nul 2>nul
if errorlevel 1 set PY=python

start "" %PY% "%ROOT%\appproxy\tray.py"
exit /b 0
