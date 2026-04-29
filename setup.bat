@echo off
REM LaunchPad - First-time setup (Windows)

echo ==^> LaunchPad Setup
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: python not found. Please install Python 3.10 or later.
    exit /b 1
)

REM Create virtual environment
if not exist ".venv" (
    echo ==^> Creating virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

REM Install dependencies
echo ==^> Installing Python dependencies...
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt

REM Install Playwright
echo ==^> Installing Playwright Chromium (this may take a minute)...
python -m playwright install chromium

REM Copy .env
if not exist ".env" (
    copy .env.example .env
    echo ==^> Created .env from template
)

REM Create directories
if not exist "users" mkdir users
if not exist "logs" mkdir logs

echo.
echo ==^> Setup complete!
echo.
echo To start LaunchPad, run:
echo    start.bat
echo.
