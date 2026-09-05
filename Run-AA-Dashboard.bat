@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo AA Efficiency Dashboard is not installed yet.
  echo Running installer...
  call Install-AA-Dashboard.bat
)
start "" ".venv\Scripts\pythonw.exe" app.py
