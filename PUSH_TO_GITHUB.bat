@echo off
REM ==========================================================================
REM  PUSH_TO_GITHUB.bat - sends this project to GitHub:
REM      https://github.com/pandi7010/line_scanner_lambton
REM
REM  Double-click it. It uses YOUR Git login on this PC.
REM  The first time, a GitHub login window may open - log in there.
REM  Run it again any time you want to send the newest code.
REM ==========================================================================
cd /d "%~dp0"
set "PATH=C:\Program Files\Git\cmd;%PATH%"
set REPO=https://github.com/pandi7010/line_scanner_lambton.git

git --version >nul 2>&1
if errorlevel 1 (
    echo Git is not installed on this PC. Install it from https://git-scm.com and try again.
    pause
    exit /b 1
)

echo.
echo === Step 1 of 4: get the folder ready for Git ===
if not exist ".git" git init
git branch -M main

REM A name for the commits - only if this PC has none yet
set GITNAME=
for /f "delims=" %%i in ('git config user.name') do set GITNAME=%%i
if "%GITNAME%"=="" git config user.name "pandi7010"
set GITMAIL=
for /f "delims=" %%i in ('git config user.email') do set GITMAIL=%%i
if "%GITMAIL%"=="" git config user.email "pandi7010@users.noreply.github.com"

echo.
echo === Step 2 of 4: save all changes (commit) ===
git add -A
git rev-parse --verify HEAD >nul 2>&1
if errorlevel 1 (
    git commit -m "Lambton line-scan camera viewer (FRONT + REAR)" -m "Layers: hardware (MindVision SDK), core (no PyQt5), ui (PyQt5), config. Live view at the real frame height with 64 channel grid lines, frame height / exposure controls, 63 ejector LEDs with delay and blow time, ejection log (seen at, LED ON at, delay set / measured), channel calibration so both cameras agree on the valve, automatic tests." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01QFtQTWozguYBxgNTqXZnyy"
) else (
    git commit -m "Update camera viewer (%DATE% %TIME%)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01QFtQTWozguYBxgNTqXZnyy"
)

echo.
echo === Step 3 of 4: connect to GitHub ===
git remote get-url origin >nul 2>&1
if errorlevel 1 git remote add origin %REPO%
git remote set-url origin %REPO%

echo.
echo === Step 4 of 4: send to GitHub (push) ===
git push -u origin main
if errorlevel 1 (
    echo.
    echo GitHub already has some files. Joining them with ours, then trying again...
    git pull origin main --allow-unrelated-histories --no-rebase --no-edit -X ours
    git push -u origin main
)
if errorlevel 1 (
    echo.
    echo *** NOT SENT. Read the messages above. ***
    echo Common reasons: not logged in to GitHub, no internet, or no permission for this repository.
) else (
    echo.
    echo *** DONE. The code is on GitHub: https://github.com/pandi7010/line_scanner_lambton ***
)
echo.
pause
