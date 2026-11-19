@echo off
REM Start the camera viewer with the REAL cameras.
REM Close lambton-main (the sorter) first - a camera can only be used by one program.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py
) else (
    python main.py
)
pause
