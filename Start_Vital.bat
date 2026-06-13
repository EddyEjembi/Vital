@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".env" (
    echo.
    echo ERROR: .env not found in this folder.
    echo Run install.bat first, then edit .env with your LLM settings.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERROR: Virtual environment not found.
    echo Run install.bat once before starting Vitál.
    echo.
    pause
    exit /b 1
)

title Vitál
echo Starting Vitál...
echo Leave this window open while you use the app. Close it to stop Vitál.
echo.

uv run python app.py
if errorlevel 1 (
    echo.
    echo Vitál exited with an error. Check .env and your LLM endpoint.
    pause
)
