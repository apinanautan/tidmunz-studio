@echo off
setlocal
cd /d "%~dp0\.."
echo ==========================================
echo   Publish Tidmun Studio Update
echo ==========================================
set /p VERSION=Version (example 1.0.1): 
set /p NOTES=Update notes: 
if "%NOTES%"=="" set "NOTES=Update and stability fixes"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish_update.ps1" -Version "%VERSION%" -Notes "%NOTES%"
if errorlevel 1 (
  echo.
  echo Publish failed. Read the error above.
  pause
  exit /b 1
)
echo.
echo Publish completed.
pause
