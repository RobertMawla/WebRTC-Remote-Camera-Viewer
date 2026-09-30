@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo WebRTC Camera Viewer - Fixed
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    py -m venv .venv
    if errorlevel 1 (
        echo Gagal membuat virtual environment.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

where cloudflared >nul 2>&1
if errorlevel 1 (
    echo.
    echo cloudflared belum terpasang.
    echo Install dengan:
    echo winget install --id Cloudflare.cloudflared
    echo.
    pause
    exit /b 1
)

echo.
python server.py

echo.
pause
