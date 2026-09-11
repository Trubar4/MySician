@echo off
setlocal enabledelayedexpansion

:: PickHero build script
:: Builds a single-file .exe via PyInstaller
:: Usage:  build.bat           — normal build
::         build.bat --clean   — wipe build/dist dirs then build

set "SPEC=pickhero.spec"
set "EXE=dist\MySician.exe"

:: ── Pre-flight checks ──────────────────────────────────────────────────────

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH. Install Python 3.10+ and try again.
    pause
    exit /b 1
)

:: ── Handle --clean flag ────────────────────────────────────────────────────

if "%~1"=="--clean" (
    echo Cleaning previous build artifacts...
    if exist build rmdir /s /q build
    if exist dist  rmdir /s /q dist
    echo Done.
    echo.
)

:: ── Install / upgrade PyInstaller ──────────────────────────────────────────

echo Installing/upgrading PyInstaller...
pip install --upgrade pyinstaller >nul 2>&1
if errorlevel 1 (
    echo ERROR: pip install pyinstaller failed. Check your Python environment.
    pause
    exit /b 1
)

:: ── The audio half of the Songsterr download ───────────────────────────────
:: yt-dlp pulls the recording the bar map was made against; ffmpeg turns what
:: YouTube serves (m4a/webm) into something SDL can play. Neither is fatal:
:: without them the .exe still builds and the download screen says in words
:: which one is missing.

echo Installing/upgrading yt-dlp...
pip install --upgrade yt-dlp >nul 2>&1
if errorlevel 1 echo WARNING: yt-dlp not installed. YouTube audio will be unavailable.

python tools\fetch_ffmpeg.py

:: ── Verify spec file exists ────────────────────────────────────────────────

if not exist "%SPEC%" (
    echo ERROR: %SPEC% not found. Run this script from the project root.
    pause
    exit /b 1
)

:: ── Stamp the build ────────────────────────────────────────────────────────
:: So the running app can say which version it is. Three fixes in a row were
:: reported as "does nothing" while their code was in the tree and tested --
:: every one of them an older EXE, and each cost a round trip to establish.

python -c "import subprocess,datetime,pathlib;sha=subprocess.run(['git','rev-parse','--short=8','HEAD'],capture_output=True,text=True).stdout.strip() or 'no-git';pathlib.Path('pickhero/_build_stamp.txt').write_text(sha+' built '+datetime.datetime.now().strftime('%%Y-%%m-%%d %%H:%%M'),encoding='utf-8')"
if exist pickhero\_build_stamp.txt (
    set /p STAMP=<pickhero\_build_stamp.txt
    echo Build stamp: !STAMP!
)

:: ── Build ──────────────────────────────────────────────────────────────────

echo.
echo Building MySician...
echo.
pyinstaller "%SPEC%" --noconfirm
if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller build failed. See output above for details.
    pause
    exit /b 1
)

:: ── Post-build summary ─────────────────────────────────────────────────────

echo.
if exist "%EXE%" (
    echo ========================================
    echo  Build succeeded!
    echo  Output: %EXE%
    for %%F in ("%EXE%") do (
        set "SIZE=%%~zF"
        set /a "MB=!SIZE! / 1048576"
        echo  Size:   ~!MB! MB
    )
    echo ========================================
    echo.
    echo Next steps:
    echo   1. Place a "songs" folder next to MySician.exe containing
    echo      your .gp3/.gp4/.gp5/.gp7/.gp8 tab files.
    echo   2. Run MySician.exe to launch the app.
    echo.
    echo Tip: Press S on the song selection screen to fetch a whole
    echo      song from Songsterr - the tab, its bar map, and the
    echo      audio of the recording that map was made against.
) else (
    echo WARNING: Build appeared to succeed but %EXE% was not found.
)

echo.
pause
