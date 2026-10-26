@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

echo [1/2] Checking Python...
python --version >nul 2>nul
if errorlevel 1 (
  echo     Python not found. Install Python 3.10+ first: https://www.python.org/downloads/
  pause
  exit /b 1
)
python --version

echo [2/2] Installing dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo     pip failed. Check your network or use a mirror:
  echo         python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  pause
  exit /b 1
)

echo.
echo Done. Next:
echo     scripts\start_tray.bat    start the tray icon (runs in background)
echo     scripts\start_gui.bat     open the main window
pause
