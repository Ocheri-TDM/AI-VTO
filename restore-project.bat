@echo off
setlocal
chcp 65001 >nul
title Souvenir Studio - restore transfer archive
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
if not exist ".migration\manifest.json" (
    echo Extract the complete AI-VTO transfer archive into a new folder first.
    pause
    exit /b 1
)
if not exist ".local\python312\python.exe" (
    echo Bundled Python runtime is missing. Extract the complete archive.
    pause
    exit /b 1
)
set "PATH=%CD%\.local\node;%PATH%"
".local\python312\python.exe" -u "scripts\restore_transfer.py"
if errorlevel 1 (
    echo Restore failed; existing data was not overwritten. See the message above.
    pause
    exit /b 1
)
if /i "%~1"=="--prepare-only" exit /b 0
call "start-project.bat" %*
exit /b %ERRORLEVEL%
