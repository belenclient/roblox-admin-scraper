@echo off
title Roblox Profile Checker
cd /d "%~dp0"

echo =============================================
echo   Roblox Profile Checker - uses your Chrome
echo =============================================
echo.

python -m pip show websocket-client >nul 2>&1
if errorlevel 1 (
    echo First run: installing a small helper (websocket-client)...
    python -m pip install --user -q websocket-client
)

echo Opening your Chrome profile checker...
python check_profiles.py %*

echo.
echo Finished this pass. Progress is saved - re-run this batch file to resume
echo from where it stopped (any new admins in admins.txt are picked up).
pause