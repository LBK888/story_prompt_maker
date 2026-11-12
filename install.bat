@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo [1/7] Checking Windows and NVIDIA GPU...
where nvidia-smi >nul 2>&1
if errorlevel 1 (
  echo ERROR: nvidia-smi was not found. Windows 11 with an NVIDIA GPU is required.
  exit /b 1
)
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

echo [2/7] Locating Python 3.11-3.14...
set "VAP_PYTHON="
for %%V in (3.14 3.13 3.12 3.11) do (
  if not defined VAP_PYTHON py -%%V -c "import sys" >nul 2>&1 && set "VAP_PYTHON=py -%%V"
)
if not defined VAP_PYTHON (
  echo ERROR: Python 3.11, 3.12, 3.13, or 3.14 was not found.
  exit /b 1
)

echo [3/7] Creating virtual environment...
if not exist ".venv\Scripts\python.exe" %VAP_PYTHON% -m venv .venv
if errorlevel 1 exit /b 1

echo [4/7] Installing locked Python dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

echo [5/7] Installing verified llama.cpp and FFmpeg runtimes...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install_runtime.ps1"
if errorlevel 1 (
  echo ERROR: Runtime download or verification failed.
  exit /b 1
)

echo [6/7] Preparing application folders...
if not exist "workflow_templates" mkdir "workflow_templates"
if not exist "projects" mkdir "projects"
if not exist "logs" mkdir "logs"
echo [7/7] Validating runtimes and Python source...
if not exist "runtime\llama.cpp\llama-server.exe" (
  echo ERROR: llama-server.exe is missing after runtime installation.
  exit /b 1
)
if not exist "runtime\ffmpeg\bin\ffmpeg.exe" (
  echo ERROR: ffmpeg.exe is missing after runtime installation.
  exit /b 1
)
if not exist "runtime\ffmpeg\bin\ffprobe.exe" (
  echo ERROR: ffprobe.exe is missing after runtime installation.
  exit /b 1
)
".venv\Scripts\python.exe" -m compileall -q app
if errorlevel 1 exit /b 1

echo.
echo Installation complete. llama.cpp, FFmpeg, and Python source were verified.
echo Run start.bat to launch the local app.
echo Select an API model or add your own GGUF files in Model Settings.
exit /b 0
