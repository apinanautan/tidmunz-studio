@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title SnapGen - Auto Setup ^& Run
color 0A

REM Keep the project root clean on every machine. These folders are required
REM by SnapGen, so hide rather than delete them. Only known disposable root
REM logs/debug dumps are removed; never touch export or snapgen_data contents.
for %%D in (.git .venv312 assets docs snapgen_data snapgen_modules tools vendor __pycache__) do (
  if exist "%%D" attrib +h "%%D" >nul 2>nul
)
if exist ".gitignore" attrib +h ".gitignore" >nul 2>nul
for %%F in (err.log out.log stderr.log stdout.log run_out.txt module_dis.txt) do (
  if exist "%%F" del /q "%%F" >nul 2>nul
)

echo ============================================
echo   SnapGen Auto Setup ^& Run
echo   Check + Install everything automatically
echo ============================================
echo.

REM ===== Step 1: Find working venv or system Python =====
echo [1/7] Checking Python...
set "VENV_PY="
set "VENV_DIR="
set "PYEXE="

REM Check .venv312 FIRST (snapgen_gui_v2.py requires Python 3.12)
if exist ".venv312\Scripts\python.exe" (
  ".venv312\Scripts\python.exe" -c "import sys; print('OK312' if sys.version_info[:2]==(3,12) else 'WRONG')" > "%TEMP%\snapgen_venv_test.txt" 2>nul
  findstr "OK312" "%TEMP%\snapgen_venv_test.txt" >nul 2>nul
  if not errorlevel 1 (
    set "VENV_PY=%CD%\.venv312\Scripts\python.exe"
    set "PYEXE=%CD%\.venv312\Scripts\python.exe"
    set "VENV_DIR=.venv312"
    echo   venv .venv312 works with Python 3.12.
    del "%TEMP%\snapgen_venv_test.txt" >nul 2>nul
    goto :found_python
  )
  echo   venv .venv312 is broken - will recreate.
  del "%TEMP%\snapgen_venv_test.txt" >nul 2>nul
  rmdir /s /q ".venv312" 2>nul
)

REM Check .venv313
if exist ".venv313\Scripts\python.exe" (
  ".venv313\Scripts\python.exe" -c "import sys; print('OK')" > "%TEMP%\snapgen_venv_test.txt" 2>nul
  findstr "OK" "%TEMP%\snapgen_venv_test.txt" >nul 2>nul
  if not errorlevel 1 (
    set "VENV_PY=%CD%\.venv313\Scripts\python.exe"
    set "PYEXE=%CD%\.venv313\Scripts\python.exe"
    set "VENV_DIR=.venv313"
    echo   venv .venv313 works.
    del "%TEMP%\snapgen_venv_test.txt" >nul 2>nul
    goto :found_python
  )
  echo   venv .venv313 is broken - will recreate.
  del "%TEMP%\snapgen_venv_test.txt" >nul 2>nul
  rmdir /s /q ".venv313" 2>nul
)

REM Check .venv311 (if 3.12/3.13 not available)
if exist ".venv311\Scripts\python.exe" (
  ".venv311\Scripts\python.exe" -c "import sys; print('OK')" > "%TEMP%\snapgen_venv_test.txt" 2>nul
  findstr "OK" "%TEMP%\snapgen_venv_test.txt" >nul 2>nul
  if not errorlevel 1 (
    set "VENV_PY=%CD%\.venv311\Scripts\python.exe"
    set "PYEXE=%CD%\.venv311\Scripts\python.exe"
    set "VENV_DIR=.venv311"
    echo   venv .venv311 works.
    del "%TEMP%\snapgen_venv_test.txt" >nul 2>nul
    goto :found_python
  )
  echo   venv .venv311 is broken - will recreate.
  del "%TEMP%\snapgen_venv_test.txt" >nul 2>nul
  rmdir /s /q ".venv311" 2>nul
)

REM No working venv - find system Python 3.12 first, then 3.11
for %%V in (3.12 3.13 3.11) do (
  if not defined PYEXE (
    for %%P in (
      "%LocalAppData%\Programs\Python\Python%%V\python.exe"
      "%ProgramFiles%\Python%%V\python.exe"
      "%ProgramFiles(x86)%\Python%%V\python.exe"
    ) do (
      if not defined PYEXE if exist %%~P (
        set "PYEXE=%%~P"
        set "PYVER=%%V"
        echo   Found: Python %%V at %%~P
      )
    )
  )
)

REM Try py launcher
if not defined PYEXE (
  py -3.12 -c "import sys; print(sys.executable)" > "%TEMP%\snapgen_py.txt" 2>nul
  if exist "%TEMP%\snapgen_py.txt" (
    for /f "delims=" %%A in (%TEMP%\snapgen_py.txt) do (
      if not defined PYEXE if not "%%A"=="" set "PYEXE=%%A"
    )
    del "%TEMP%\snapgen_py.txt" >nul 2>nul
  )
)

REM Try plain python
if not defined PYEXE (
  python -c "import sys; print(sys.executable)" > "%TEMP%\snapgen_py.txt" 2>nul
  if exist "%TEMP%\snapgen_py.txt" (
    for /f "delims=" %%A in (%TEMP%\snapgen_py.txt) do (
      if not defined PYEXE if not "%%A"=="" set "PYEXE=%%A"
    )
    del "%TEMP%\snapgen_py.txt" >nul 2>nul
  )
)

REM Try where python (finds Python in PATH even if not in standard locations)
if not defined PYEXE (
  where python > "%TEMP%\snapgen_where.txt" 2>nul
  if exist "%TEMP%\snapgen_where.txt" (
    for /f "delims=" %%A in (%TEMP%\snapgen_where.txt) do (
      if not defined PYEXE if not "%%A"=="" (
        "%%A" -c "import sys; print('OK')" >nul 2>nul
        if not errorlevel 1 set "PYEXE=%%A"
      )
    )
    del "%TEMP%\snapgen_where.txt" >nul 2>nul
  )
)

REM Try uv-managed Python 3.12 (works on machines without winget)
if not defined PYEXE (
  where uv > "%TEMP%\snapgen_uv_where.txt" 2>nul
  if exist "%TEMP%\snapgen_uv_where.txt" (
    for /f "delims=" %%U in (%TEMP%\snapgen_uv_where.txt) do if not defined UVEXE set "UVEXE=%%U"
    del "%TEMP%\snapgen_uv_where.txt" >nul 2>nul
  )
  if defined UVEXE (
    echo   Found uv: !UVEXE!
    !UVEXE! python find 3.12 > "%TEMP%\snapgen_uv_py312.txt" 2>nul
    for /f "delims=" %%A in (%TEMP%\snapgen_uv_py312.txt) do if not defined PYEXE set "PYEXE=%%A"
    del "%TEMP%\snapgen_uv_py312.txt" >nul 2>nul
    if not defined PYEXE (
      echo   uv did not have Python 3.12 cached - downloading Python 3.12...
      !UVEXE! python install 3.12
      !UVEXE! python find 3.12 > "%TEMP%\snapgen_uv_py312.txt" 2>nul
      for /f "delims=" %%A in (%TEMP%\snapgen_uv_py312.txt) do if not defined PYEXE set "PYEXE=%%A"
      del "%TEMP%\snapgen_uv_py312.txt" >nul 2>nul
    )
    if defined PYEXE echo   Found Python 3.12 via uv: !PYEXE!
  )
)

REM No Python - try winget install
REM First bootstrap uv directly. This works on Windows machines that do not
REM have Git, Python, or winget yet.
if not defined PYEXE (
  echo   Python not found. Bootstrapping portable uv...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { irm https://astral.sh/uv/install.ps1 | iex; exit 0 } catch { Write-Host $_; exit 1 }"
  if exist "%USERPROFILE%\.local\bin\uv.exe" set "UVEXE=%USERPROFILE%\.local\bin\uv.exe"
  if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UVEXE=%USERPROFILE%\.cargo\bin\uv.exe"
  if defined UVEXE (
    !UVEXE! python install 3.12
    !UVEXE! python find 3.12 > "%TEMP%\snapgen_uv_py312.txt" 2>nul
    for /f "delims=" %%A in (%TEMP%\snapgen_uv_py312.txt) do if not defined PYEXE set "PYEXE=%%A"
    del "%TEMP%\snapgen_uv_py312.txt" >nul 2>nul
  )
)

REM Final fallback: Windows Package Manager when available.
if not defined PYEXE (
  echo   Python not found. Installing via winget...
  where winget >nul 2>nul
  if not errorlevel 1 (
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  )
  for %%V in (3.12) do (
    if not defined PYEXE (
      for %%P in (
        "%LocalAppData%\Programs\Python\Python%%V\python.exe"
        "%ProgramFiles%\Python\Python%%V\python.exe"
      ) do (
        if not defined PYEXE if exist %%~P set "PYEXE=%%~P"
      )
    )
  )
)

if not defined PYEXE (
  echo.
  echo [FAIL] Cannot find or install Python.
  echo   Automatic download failed. Please check internet access, then run this file again.
  echo   Manual fallback: install Python 3.12 from https://python.org
  echo   Make sure to check "Add Python to PATH" during install.
  echo.
  pause
  exit /b 1
)

:found_python
set "PYIS312=0"
"%PYEXE%" -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>nul
if not errorlevel 1 set "PYIS312=1"

REM If found Python is not 3.12, try to get 3.12
if "%PYIS312%"=="0" (
  echo   Current Python is not 3.12 - searching for Python 3.12...
  set "PYEXE="
  for %%P in (
    "%LocalAppData%\Programs\Python\Python312\python.exe"
    "%ProgramFiles%\Python\Python312\python.exe"
  ) do (
    if not defined PYEXE if exist %%~P set "PYEXE=%%~P"
  )
  if not defined PYEXE (
    py -3.12 -c "import sys; print('OK')" >nul 2>nul
    if not errorlevel 1 (
      py -3.12 -c "import sys; print(sys.executable)" > "%TEMP%\snapgen_py312.txt" 2>nul
      for /f "delims=" %%A in (%TEMP%\snapgen_py312.txt) do set "PYEXE=%%A"
      del "%TEMP%\snapgen_py312.txt" >nul 2>nul
    )
  )
  if not defined PYEXE (
    where uv > "%TEMP%\snapgen_uv_where.txt" 2>nul
    if exist "%TEMP%\snapgen_uv_where.txt" (
      for /f "delims=" %%U in (%TEMP%\snapgen_uv_where.txt) do if not defined UVEXE set "UVEXE=%%U"
      del "%TEMP%\snapgen_uv_where.txt" >nul 2>nul
    )
    if defined UVEXE (
      echo   Python 3.12 not found in system paths. Trying uv...
      !UVEXE! python find 3.12 > "%TEMP%\snapgen_uv_py312.txt" 2>nul
      for /f "delims=" %%A in (%TEMP%\snapgen_uv_py312.txt) do if not defined PYEXE set "PYEXE=%%A"
      del "%TEMP%\snapgen_uv_py312.txt" >nul 2>nul
      if not defined PYEXE (
        echo   uv downloading Python 3.12...
        !UVEXE! python install 3.12
        !UVEXE! python find 3.12 > "%TEMP%\snapgen_uv_py312.txt" 2>nul
        for /f "delims=" %%A in (%TEMP%\snapgen_uv_py312.txt) do if not defined PYEXE set "PYEXE=%%A"
        del "%TEMP%\snapgen_uv_py312.txt" >nul 2>nul
      )
      if defined PYEXE echo   Found Python 3.12 via uv: !PYEXE!
    )
  )
  if not defined PYEXE (
    echo   uv not found. Downloading portable uv bootstrap...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { irm https://astral.sh/uv/install.ps1 | iex; exit 0 } catch { Write-Host $_; exit 1 }"
    if exist "%USERPROFILE%\.local\bin\uv.exe" set "UVEXE=%USERPROFILE%\.local\bin\uv.exe"
    if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UVEXE=%USERPROFILE%\.cargo\bin\uv.exe"
    if defined UVEXE (
      !UVEXE! python install 3.12
      !UVEXE! python find 3.12 > "%TEMP%\snapgen_uv_py312.txt" 2>nul
      for /f "delims=" %%A in (%TEMP%\snapgen_uv_py312.txt) do if not defined PYEXE set "PYEXE=%%A"
      del "%TEMP%\snapgen_uv_py312.txt" >nul 2>nul
    )
  )
  if not defined PYEXE (
    echo   Python 3.12 not found. Installing via winget...
    where winget >nul 2>nul
    if not errorlevel 1 winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    for %%P in (
      "%LocalAppData%\Programs\Python\Python312\python.exe"
      "%ProgramFiles%\Python\Python312\python.exe"
    ) do (
      if not defined PYEXE if exist %%~P set "PYEXE=%%~P"
    )
  )
  if not defined PYEXE (
    echo [FAIL] Cannot find Python 3.12. SnapGen requires Python 3.12.
    echo   Please install Python 3.12 from https://python.org
    pause
    exit /b 1
  )
  echo   Found Python 3.12: %PYEXE%
  set "PYIS312=1"
  REM If we had a working venv but it's wrong version, recreate as .venv312
  if defined VENV_DIR (
    echo   Removing wrong-version venv: %VENV_DIR%
    rmdir /s /q "%VENV_DIR%" 2>nul
    set "VENV_DIR="
    set "VENV_PY="
  )
)

if defined VENV_PY (
  set "PYEXE=%VENV_PY%"
) else (
  set "VENV_PY=%PYEXE%"
)
echo   Using: %PYEXE%
"%PYEXE%" --version

REM ===== Step 2: Check tkinter =====
echo.
echo [2/7] Checking tkinter...
"%PYEXE%" -c "import tkinter; print('tkinter OK')" >nul 2>nul
if errorlevel 1 (
  echo   tkinter missing. Installing official Python 3.12 with Tcl/Tk...
  set "PY_ARCH=amd64"
  if /I "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "PY_ARCH=arm64"
  set "PY_INSTALLER=%TEMP%\python-3.12.10-!PY_ARCH!.exe"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-!PY_ARCH!.exe' -OutFile '!PY_INSTALLER!'"
  if exist "!PY_INSTALLER!" (
    "!PY_INSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_tcltk=1 Include_launcher=1 Include_test=0 TargetDir="%LocalAppData%\Programs\Python\Python312"
    del "!PY_INSTALLER!" >nul 2>nul
    if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYEXE=%LocalAppData%\Programs\Python\Python312\python.exe"
    if defined VENV_DIR (
      rmdir /s /q "!VENV_DIR!" >nul 2>nul
      set "VENV_DIR="
      set "VENV_PY="
    )
  )
  "!PYEXE!" -c "import tkinter; print('tkinter OK')" >nul 2>nul
  if errorlevel 1 (
    echo [FAIL] Automatic Python/Tkinter installation failed.
    echo   Check internet access and run setup_and_run.bat again.
    pause
    exit /b 1
  )
)
echo   tkinter OK

REM ===== Step 3: Create venv (if not using working existing one) =====
echo.
echo [3/7] Setting up venv...
if not defined VENV_DIR (
  REM snapgen_gui_v2.py REQUIRES Python 3.12 (.pyc compiled for 3.12)
  REM Check if current Python is 3.12
  "%PYEXE%" -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>nul
  if errorlevel 1 (
    if defined PYVER (
      echo   Python found is %PYVER%, need 3.12. Trying winget install...
    ) else (
      echo   Python found is not 3.12. Trying winget install 3.12...
    )
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements 2>nul
    REM Re-scan for 3.12
    for %%P in (
      "%LocalAppData%\Programs\Python\Python312\python.exe"
      "%ProgramFiles%\Python\Python312\python.exe"
    ) do (
      if exist %%~P set "PYEXE=%%~P"
    )
    "%PYEXE%" -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>nul
    if errorlevel 1 (
      echo [FAIL] Need Python 3.12. Please install from https://python.org
      pause
      exit /b 1
    )
  )
  set "VENV_DIR=.venv312"
  set "VENV_PY=%CD%\!VENV_DIR!\Scripts\python.exe"
  echo   Creating venv: !VENV_DIR!...
  "%PYEXE%" -m venv "!VENV_DIR!"
  if not exist "!VENV_PY!" (
    echo [FAIL] venv creation failed.
    pause
    exit /b 1
  )
  echo   venv created: !VENV_DIR!
) else (
  echo   venv exists: %VENV_DIR%
)

REM ===== Step 4: Install packages =====
echo.
echo [4/7] Installing packages...
"%VENV_PY%" -m pip --version >nul 2>nul
if errorlevel 1 (
  echo   pip missing in venv - installing pip...
  "%VENV_PY%" -m ensurepip --upgrade 2>nul
  if errorlevel 1 (
    echo   ensurepip failed - downloading get-pip.py...
    powershell -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%TEMP%\get-pip.py'"
    "%VENV_PY%" "%TEMP%\get-pip.py"
    del "%TEMP%\get-pip.py" >nul 2>nul
  )
)

"%VENV_PY%" -m pip install --upgrade pip wheel setuptools -q 2>nul
echo   Installing Pillow...
"%VENV_PY%" -m pip install Pillow -q
if errorlevel 1 (
  echo   [WARN] Pillow install failed, retrying...
  "%VENV_PY%" -m pip install Pillow
  if errorlevel 1 (
    echo [FAIL] Cannot install Pillow.
    echo   Try manually: %VENV_PY% -m pip install Pillow
    pause
    exit /b 1
  )
)
echo   Pillow OK

echo   Installing websocket-client for SnapGen Browser capture...
"%VENV_PY%" -m pip install websocket-client -q
if errorlevel 1 (
  echo   [WARN] websocket-client install failed, retrying...
  "%VENV_PY%" -m pip install websocket-client
  if errorlevel 1 (
    echo [FAIL] Cannot install websocket-client.
    echo   Try manually: %VENV_PY% -m pip install websocket-client
    pause
    exit /b 1
  )
)
echo   websocket-client OK

echo   Installing qcloud_cos for Prop 3D...
"%VENV_PY%" -m pip install cos-python-sdk-v5 -q
if errorlevel 1 (
  echo   [WARN] cos-python-sdk-v5 install failed, retrying...
  "%VENV_PY%" -m pip install cos-python-sdk-v5
  if errorlevel 1 (
    echo [FAIL] Cannot install cos-python-sdk-v5.
    echo   Try manually: %VENV_PY% -m pip install cos-python-sdk-v5
    pause
    exit /b 1
  )
)
"%VENV_PY%" -c "import qcloud_cos; print('qcloud_cos OK')" >nul 2>nul
if errorlevel 1 (
  echo [FAIL] cos-python-sdk-v5 installed but qcloud_cos import still failed.
  pause
  exit /b 1
)
echo   qcloud_cos OK

REM GitHub CLI sends privacy-filtered error reports from every workstation.
REM Missing/login failures never block SnapGen; reports remain queued locally.
where gh >nul 2>nul
if errorlevel 1 if exist "snapgen_data\tools\gh\bin\gh.exe" set "PATH=%CD%\snapgen_data\tools\gh\bin;%PATH%"
where gh >nul 2>nul
if errorlevel 1 (
  where winget >nul 2>nul
  if not errorlevel 1 (
    echo   Installing GitHub CLI for automatic error reports...
    winget install --id GitHub.cli -e --accept-package-agreements --accept-source-agreements --silent >nul 2>nul
  )
)
where gh >nul 2>nul
if errorlevel 1 (
  echo   [WARN] GitHub CLI not ready. Errors will stay queued until Repair installs it.
) else (
  gh auth status --hostname github.com >nul 2>nul
  if errorlevel 1 (
    echo   Opening browser once for automatic error reports...
    gh auth login --hostname github.com --git-protocol https --web --clipboard
    gh auth status --hostname github.com >nul 2>nul
    if errorlevel 1 echo   [WARN] Browser approval not finished. SnapGen will ask again only when an error must be sent.
  ) else (
    echo   GitHub error reporting ready
  )
)

REM ===== Step 5: Verify everything =====
echo.
REM ===== Step 5: Verify project files =====
echo.
echo [5/7] Checking SnapGen project files...
set "MISSING_PROJECT=0"

if not exist "snapgen_gui_v2.py" (
  echo   [FAIL] Missing: snapgen_gui_v2.py
  set "MISSING_PROJECT=1"
)
if not exist "__pycache__\snapgen_gui_v2.cpython-312.pyc" (
  if not exist "__pycache__\snapgen_core.cpython-312.pyc" (
    echo   [FAIL] Missing: __pycache__\snapgen_core.cpython-312.pyc
  echo          This app needs the recovered Python 3.12 bytecode file.
  set "MISSING_PROJECT=1"
  )
)
if not exist "snapgen_modules\snapgen_page_video.py" (
  echo   [FAIL] Missing: snapgen_modules\snapgen_page_video.py
  set "MISSING_PROJECT=1"
)
if not exist "snapgen_modules\snapgen_page_image.py" (
  echo   [FAIL] Missing: snapgen_modules\snapgen_page_image.py
  set "MISSING_PROJECT=1"
)
if not exist "snapgen_modules\snapgen_page_prop.py" (
  echo   [FAIL] Missing: snapgen_modules\snapgen_page_prop.py
  set "MISSING_PROJECT=1"
)
if not exist "snapgen_modules\snapgen_error_reporter.py" (
  echo   [FAIL] Missing: snapgen_modules\snapgen_error_reporter.py
  set "MISSING_PROJECT=1"
)
if not exist "snapgen_modules\snapgen_bridge_cursor_patch.py" (
  echo   [FAIL] Missing: snapgen_modules\snapgen_bridge_cursor_patch.py
  set "MISSING_PROJECT=1"
)
if not exist "snapgen_modules\ai_slow2x.py" (
  echo   [FAIL] Missing: snapgen_modules\ai_slow2x.py
  set "MISSING_PROJECT=1"
)
if not exist "snapgen_data\snapgen_config.json" (
  echo   [FAIL] Missing: snapgen_data\snapgen_config.json
  set "MISSING_PROJECT=1"
)
if not exist "snapgen_data\prompt_bank.txt" (
  echo   [WARN] Missing: snapgen_data\prompt_bank.txt
)
if not exist "assets\tidmun_studio_icon_final.ico" (
  echo   [WARN] Missing: assets\tidmun_studio_icon_final.ico
)
if not exist "snapgen_data\tools\ffmpeg\ffmpeg.exe" (
  echo   Installing portable FFmpeg for RIFE video decode/encode...
  ".venv312\Scripts\python.exe" -B -c "import sys; sys.path.insert(0, r'snapgen_modules'); import ai_slow2x; print(ai_slow2x.ensure_ffmpeg_tool(print))"
  if errorlevel 1 echo   [WARN] FFmpeg install failed. Slow 2x will retry when first used.
) else (
  echo   bundled ffmpeg OK
)
if not exist "snapgen_data\tools\rife-ncnn-vulkan\rife-ncnn-vulkan.exe" (
  echo   Installing RIFE AI Slow 2x package for this PC...
  ".venv312\Scripts\python.exe" -B -c "import sys; sys.path.insert(0, r'snapgen_modules'); import ai_slow2x; print(ai_slow2x.ensure_rife_tool(print))"
  if errorlevel 1 echo   [WARN] RIFE install failed. Slow 2x will retry when first used.
) else (
  echo   bundled RIFE OK
)
if not exist "snapgen_data\hunyuan_cookies.txt" (
  echo   [WARN] Missing: snapgen_data\hunyuan_cookies.txt
  echo          Prop 3D may need cookies/account setup on the new machine.
)
if not exist "vendor\tkinterdnd2\__init__.py" (
  echo   [WARN] Missing vendored vendor\tkinterdnd2 folder.
  echo          Drag/drop features may not work unless installed separately.
)

if "%MISSING_PROJECT%"=="1" (
  echo.
  echo [FAIL] Project files are incomplete.
  echo   Copy the whole Project snapgen.ai folder, not only this .bat file.
  echo.
  pause
  exit /b 1
)

if not exist "export" mkdir "export" >nul 2>nul
for %%D in (image video ref prop face karaoke) do (
  if not exist "export\%%D" mkdir "export\%%D" >nul 2>nul
)
echo   Project files OK

REM ===== Step 6: Verify everything =====
echo.
echo [6/7] Verifying Python imports...
"%VENV_PY%" -c "import tkinter; import PIL; import qcloud_cos; import os, sys, json, re, subprocess, threading, tempfile, pathlib, base64, shutil, time, socket, zipfile; print('All imports OK')"
if errorlevel 1 (
  echo [FAIL] Some imports failed.
  "%VENV_PY%" -c "import tkinter; print('tkinter OK')" 2>nul || echo "  Missing: tkinter"
  "%VENV_PY%" -c "import PIL; print('PIL OK')" 2>nul || echo "  Missing: PIL/Pillow"
  "%VENV_PY%" -c "import qcloud_cos; print('qcloud_cos OK')" 2>nul || echo "  Missing: qcloud_cos"
  pause
  exit /b 1
)
echo   All imports OK

REM Create a portable launcher shortcut bound to this project's Python.
REM This avoids broken/missing Windows .py file associations on other PCs.
set "PYW_EXE=%CD%\%VENV_DIR%\Scripts\pythonw.exe"
if not exist "%PYW_EXE%" set "PYW_EXE=%CD%\%VENV_DIR%\Scripts\python.exe"
set "PROJECT_DIR=%CD%"
set "SHORTCUT_PATH=%CD%\ติดมันส์ สตูดิโอ.lnk"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $s=$ws.CreateShortcut($env:SHORTCUT_PATH); $s.TargetPath=$env:PYW_EXE; $q=[char]34; $s.Arguments='-B '+$q+$env:PROJECT_DIR+'\snapgen_gui_v2.py'+$q; $s.WorkingDirectory=$env:PROJECT_DIR; $ico=$env:PROJECT_DIR+'\assets\tidmun_studio_icon_final.ico'; if(Test-Path -LiteralPath $ico){$s.IconLocation=$ico}; $s.Save()" >nul 2>nul
if exist "%SHORTCUT_PATH%" (
  echo   Launcher shortcut ready: ติดมันส์ สตูดิโอ.lnk
) else (
  echo   [WARN] Could not create launcher shortcut. setup_and_run.bat still works.
)

REM Register .py for the current Windows user so snapgen_gui_v2.py can be
REM double-clicked on PCs where Python file association was never installed.
REM Use pythonw.exe to avoid opening a terminal window.
set "PY_ASSOC_EXE=%CD%\%VENV_DIR%\Scripts\pythonw.exe"
if not exist "%PY_ASSOC_EXE%" set "PY_ASSOC_EXE=%PYW_EXE%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$base='HKCU:\Software\Classes'; New-Item -Path ($base+'\.py') -Force | Out-Null; Set-Item -Path ($base+'\.py') -Value 'SnapGen.Python.File'; New-Item -Path ($base+'\SnapGen.Python.File\shell\open\command') -Force | Out-Null; $q=[char]34; $cmd=$q+$env:PY_ASSOC_EXE+$q+' '+$q+'%%1'+$q+' %%*'; Set-Item -Path ($base+'\SnapGen.Python.File\shell\open\command') -Value $cmd; New-Item -Path ($base+'\SnapGen.Python.File\DefaultIcon') -Force | Out-Null; Set-Item -Path ($base+'\SnapGen.Python.File\DefaultIcon') -Value ($env:PY_ASSOC_EXE+',0')" >nul 2>nul
if errorlevel 1 (
  echo   [WARN] Could not register .py for this Windows user.
) else (
  echo   Python .py double-click ready - uses pythonw.exe without terminal.
)

where curl >nul 2>nul
if errorlevel 1 (
  echo   [WARN] curl not found - needed for Bridge API calls.
) else (
  echo   curl OK
)

where tailscale >nul 2>nul
if errorlevel 1 (
  echo   [WARN] Tailscale not found - SnapGen still checks the shared Tailscale login.
  echo          Open Settings and press Auto Repair to install it automatically.
) else (
  echo   Tailscale OK - this machine uses its own Bridge: 127.0.0.1:8000
)

if /I "%~1"=="--check" (
  echo.
  echo ============================================
  echo   Check complete. SnapGen is ready to run.
  echo ============================================
  echo.
  pause
  exit /b 0
)
if /I "%~1"=="--no-run" (
  exit /b 0
)

REM ===== Step 7: Run SnapGen =====
echo.
echo [7/7] Starting SnapGen...
echo ============================================
echo.
set "VENV_PYW=%CD%\%VENV_DIR%\Scripts\pythonw.exe"
if not exist "%VENV_PYW%" set "VENV_PYW=%CD%\%VENV_DIR%\Scripts\python.exe"
if not exist "%VENV_PYW%" (
  echo [FAIL] Missing project Python: %VENV_PYW%
  pause
  exit /b 1
)
start "" "%VENV_PYW%" -B "%CD%\snapgen_gui_v2.py"
if errorlevel 1 (
  echo [FAIL] Cannot start SnapGen with project python.exe.
  pause
  exit /b 1
)
echo   SnapGen started without a console window.
exit /b 0
