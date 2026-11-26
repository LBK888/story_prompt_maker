@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run install.bat first.
  goto failed
)
".venv\Scripts\python.exe" run.py
if errorlevel 1 goto failed
exit /b 0

:failed
pause
exit /b 1
