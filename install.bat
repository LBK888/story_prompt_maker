@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto dependencies
for %%V in (3.14 3.13 3.12 3.11) do (
  py -%%V -c "import sys" >nul 2>&1
  if not errorlevel 1 (
    py -%%V -m venv .venv
    if errorlevel 1 goto failed
    goto dependencies
  )
)
python -c "import sys; sys.exit(not ((3,11) <= sys.version_info[:2] <= (3,14)))"
if errorlevel 1 (
  echo Python 3.11 through 3.14 is required on PATH or via py.
  goto failed
)
python -m venv .venv
if errorlevel 1 goto failed

:dependencies
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
set "MEDIA_FLAG=-FfmpegOnly"
set "VAP_INITIAL_PROVIDER=openrouter"
nvidia-smi --query-gpu=name --format=csv,noheader >nul 2>&1
if not errorlevel 1 (
  set "MEDIA_FLAG="
  set "VAP_INITIAL_PROVIDER=local"
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install_runtime.ps1" %MEDIA_FLAG%
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "from app.main import app"
if errorlevel 1 goto failed
echo Installation complete. Launch start.bat.
exit /b 0

:failed
echo Installation failed. See the error above.
pause
exit /b 1
