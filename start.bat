@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: Virtual environment not found. Run install.bat first.
  pause
  exit /b 1
)
if not exist "runtime\llama.cpp\llama-server.exe" (
  echo ERROR: llama-server.exe is missing. Run install.bat again.
  pause
  exit /b 1
)
if not exist "runtime\ffmpeg\bin\ffmpeg.exe" (
  echo ERROR: FFmpeg is missing. Run install.bat again.
  pause
  exit /b 1
)
if not exist "runtime\ffmpeg\bin\ffprobe.exe" (
  echo ERROR: FFprobe is missing. Run install.bat again.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -c "import fastapi,uvicorn,httpx,jinja2,PIL,opencc" >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python dependencies are incomplete. Run install.bat again.
  pause
  exit /b 1
)

set "VAP_PORT=7865"
for /f "usebackq delims=" %%P in (`".venv\Scripts\python.exe" -c "from app.config import load_config; print(load_config().port)"`) do set "VAP_PORT=%%P"
netstat -ano | findstr /R /C:":%VAP_PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo ERROR: Port %VAP_PORT% is already in use. Stop the existing process or change config\app.json.
  pause
  exit /b 1
)

echo Starting Vid Auto Prompt at http://127.0.0.1:%VAP_PORT%
echo ComfyUI is not started or managed by this application.
".venv\Scripts\python.exe" run.py
exit /b %errorlevel%
