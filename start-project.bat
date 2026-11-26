@echo off
setlocal
chcp 65001 >nul
title Souvenir Studio - services and request activity
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
if exist ".local\node\node.exe" set "PATH=%CD%\.local\node;%PATH%"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found: .venv\Scripts\python.exe
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -u "scripts\run_project.py" %*
set "PROJECT_EXIT=%ERRORLEVEL%"
if not "%PROJECT_EXIT%"=="0" pause
exit /b %PROJECT_EXIT%
