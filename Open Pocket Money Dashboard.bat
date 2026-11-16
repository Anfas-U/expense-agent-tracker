@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. See README.md for setup instructions.
    pause
    exit /b 1
)

set "SERVER_RUNNING="
for /f "tokens=*" %%A in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do set "SERVER_RUNNING=1"

if not defined SERVER_RUNNING (
    start "Pocket Money Dashboard" ".venv\Scripts\python.exe" -m src.interfaces.web
    for /l %%I in (1,1,20) do (
        timeout /t 1 /nobreak >nul
        set "SERVER_RUNNING="
        for /f "tokens=*" %%A in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do set "SERVER_RUNNING=1"
        if defined SERVER_RUNNING goto open_dashboard
    )
    echo The dashboard did not start. Check the Pocket Money Dashboard window for errors.
    pause
    exit /b 1
)

:open_dashboard
start "" "http://127.0.0.1:8000"
