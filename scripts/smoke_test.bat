@echo off
REM LaunchPad smoke test for Windows.
REM Requires the server to already be running (start.bat in another terminal).
REM Uses PowerShell Invoke-WebRequest so no curl dependency.

setlocal EnableDelayedExpansion

if "%LAUNCHPAD_PORT%"=="" set LAUNCHPAD_PORT=7070
if "%LAUNCHPAD_HOST%"=="" set LAUNCHPAD_HOST=localhost
set BASE=http://%LAUNCHPAD_HOST%:%LAUNCHPAD_PORT%

set /a pass=0
set /a fail=0

echo.
echo LaunchPad smoke test against %BASE%
echo --------------------------------------------

call :check "Health check"              "/api/health"           "200"
call :check "Network info"               "/api/network"          "200"
call :check "Profiles list"              "/api/profiles"         "200"
call :check "Static index.html"          "/"                     "200"
call :check "Auth me (should reject)"    "/api/auth/me"          "401"
call :check "Listings (should reject)"   "/api/listings"         "401"
call :check "Settings (should reject)"   "/api/settings"         "401"
call :check "Gmail (should reject)"      "/api/gmail/status"     "401"
call :check "Reminders (should reject)"  "/api/reminders"        "401"
call :check "Companies (should reject)"  "/api/companies"        "401"
call :check "History (should reject)"    "/api/history"          "401"
call :check "Backup export (reject)"     "/api/backup/export"    "401"
call :check "OpenAPI schema"             "/openapi.json"         "200"

echo --------------------------------------------
echo   Passed: %pass%   Failed: %fail%
echo.
if %fail% GTR 0 exit /b 1
echo All smoke checks passed.
goto :eof

:check
set "name=%~1"
set "path=%~2"
set "expected=%~3"
for /f %%i in ('powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri '%BASE%%path%' -ErrorAction Stop; $r.StatusCode } catch { $_.Exception.Response.StatusCode.Value__ }"') do set code=%%i
if "%code%"=="%expected%" (
    echo   [OK]   %name%  [%code%]
    set /a pass+=1
) else (
    echo   [FAIL] %name%  [%code%]  expected %expected%
    set /a fail+=1
)
goto :eof
