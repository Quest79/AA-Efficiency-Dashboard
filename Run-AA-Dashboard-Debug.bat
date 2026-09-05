@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call Install-AA-Dashboard.bat
".venv\Scripts\python.exe" app.py
pause
