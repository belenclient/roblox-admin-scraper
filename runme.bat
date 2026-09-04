@echo off
title Roblox Admin Scraper
cd /d "%~dp0"

set PY=python
where python >nul 2>nul
if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python is not installed or not on PATH.
        echo Install it from https://www.python.org/downloads/ and tick "Add Python to PATH".
        pause
        exit /b 1
    )
    set PY=py -3
)

echo == Installing requirements ==
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install requirements.
    pause
    exit /b 1
)

echo == Starting scraper ==
%PY% scraper.py

echo.
pause