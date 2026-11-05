@echo off
cd /d "%~dp0"
set PYTHONNOUSERSITE=1
if not exist ".conda\python.exe" (
    echo Conda environment missing. Read README.md first.
    pause
    exit /b 1
)
".conda\python.exe" -s -m uvicorn ai_pulse.app:app --host 127.0.0.1 --port 8765 --workers 1
pause
