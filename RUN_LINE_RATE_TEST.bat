@echo off
REM Line rate test: does a slower line rate reduce line / frame loss? (about 1 minute)
REM Close the viewer and lambton-main first - a camera can be used by one program only.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" tools\line_rate_test.py %*
) else (
    python tools\line_rate_test.py %*
)
pause
