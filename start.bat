@echo off
REM LaunchPad - Start server (Windows)

if not exist ".venv" (
    echo Error: .venv not found. Run setup.bat first.
    exit /b 1
)

call .venv\Scripts\activate.bat

REM Load .env
if exist ".env" (
    for /f "delims=" %%i in (.env) do (
        set "%%i"
    )
)

if "%LAUNCHPAD_PORT%"=="" set LAUNCHPAD_PORT=7070
if "%LAUNCHPAD_HOST%"=="" set LAUNCHPAD_HOST=0.0.0.0

python -m uvicorn server:app --host %LAUNCHPAD_HOST% --port %LAUNCHPAD_PORT% --reload
