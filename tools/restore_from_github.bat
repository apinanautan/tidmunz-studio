@echo off
setlocal
set "REPO=https://github.com/tidmunzsocial-lab/tidmunz-studio.git"
for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"
if exist "%PROJECT_ROOT%\setup_and_run.bat" (
  set "TARGET=%PROJECT_ROOT%"
) else (
  set "TARGET=%~dp0SnapGen"
)

echo SnapGen recovery from GitHub
echo Target: %TARGET%

where git >nul 2>nul
if errorlevel 1 (
  echo Git is required. Install Git for Windows, then run this file again.
  start "" "https://git-scm.com/download/win"
  pause
  exit /b 1
)

if exist "%TARGET%\.git" (
  echo Existing installation found. Updating...
  git -C "%TARGET%" pull --ff-only
  if errorlevel 1 goto fail
) else (
  if exist "%TARGET%" (
    echo Target folder already exists but is not a Git checkout:
    echo %TARGET%
    goto fail
  )
  git clone "%REPO%" "%TARGET%"
  if errorlevel 1 goto fail
)

if exist "%TARGET%\setup_and_run.bat" (
  call "%TARGET%\setup_and_run.bat"
  exit /b %errorlevel%
)

echo setup_and_run.bat was not found in the downloaded repository.

:fail
echo Recovery failed. Check the internet connection and try again.
pause
exit /b 1
