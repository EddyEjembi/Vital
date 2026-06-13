@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ========================================
echo   Vitál - one-time setup
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.12 or newer is required but was not found on PATH.
    echo Install from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

echo [1/3] Checking for uv...
where uv >nul 2>&1
if errorlevel 1 (
    echo       uv not found — installing with pip...
    python -m pip install --upgrade pip uv
    if errorlevel 1 (
        echo ERROR: Could not install uv. Check your internet connection and try again.
        pause
        exit /b 1
    )
)

echo [2/3] Installing packages into .venv ^(uv sync^)...
uv sync
if errorlevel 1 (
    python -m uv sync
    if errorlevel 1 (
        echo ERROR: uv sync failed.
        pause
        exit /b 1
    )
)

echo [3/3] Environment file...
if exist ".env" (
    echo       .env already exists — leaving your file unchanged.
) else (
    if not exist ".env.example" (
        echo ERROR: .env.example is missing from this folder.
        pause
        exit /b 1
    )
    copy /Y ".env.example" ".env" >nul
    echo       Created .env from .env.example.
    echo.
    echo  IMPORTANT: Before your first run, edit .env with your real settings:
    echo    - VITAL_LLM_BASE_URL
    echo    - VITAL_MODEL_ID
    echo.
    echo  Choose an app to edit .env ^(VS Code, Notepad, or any editor^).
    echo.
    rundll32.exe shell32.dll,OpenAs_RunDLL "%CD%\.env"
    echo.
    echo  Save .env when you are done, then close this window.
    pause
)

echo.
echo ========================================
echo   Setup complete.
echo.
echo   Double-click "Start_Vital.bat" each day to run Vitál.
echo ========================================
echo.
pause
