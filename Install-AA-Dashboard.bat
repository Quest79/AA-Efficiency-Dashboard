@echo off
setlocal
cd /d "%~dp0"
echo.
echo  AA Efficiency Dashboard - Installer
echo  ===================================
echo.
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3 was not found.
  echo Install Python 3.11+ from python.org and enable "Add Python to PATH".
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python environment...
  py -3 -m venv .venv
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo Installing Playwright Chromium as a fallback browser...
python -m playwright install chromium
echo.
echo Installation complete.
echo Run Run-AA-Dashboard.bat
pause
