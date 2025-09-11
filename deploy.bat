@echo off
echo ========================================
echo   MSTS Consist Editor - Deployment Guide
echo ========================================
echo.
echo Choose your deployment method:
echo.
echo [1] Full Source + Auto-Setup (Recommended for developers)
echo     - Includes all source code
echo     - Automatic setup on target computer
echo     - Requires Python on target computer
echo     - File: ~50MB (full folder)
echo.
echo [2] Standalone Executable (Recommended for end-users)
echo     - Single .exe file, no setup required
echo     - No Python installation needed
echo     - File: ~100MB (compressed executable)
echo     - Run build_installer.bat first
echo.
echo [3] Test Current Setup
echo     - Verify everything works locally
echo.
set /p choice="Enter your choice (1-3): "

if "%choice%"=="1" (
    echo.
    echo Creating source package...
    echo.
    echo To create the source package:
    echo 1. Zip the entire OpenRailDevlopment folder
    echo 2. Exclude: .git/, __pycache__/, *.log, .venv/
    echo 3. Share the zip file
    echo.
    echo Recipients run: setup.bat then run_consist_editor.bat
    echo.
)

if "%choice%"=="2" (
    echo.
    echo Building standalone executable...
    echo.
    call build_installer.bat
    echo.
    echo To distribute:
    echo 1. Copy dist\MSTS_Consist_Editor.exe
    echo 2. Add README.md
    echo 3. Zip together
    echo 4. Share the zip file
    echo.
    echo Recipients just double-click the .exe file!
    echo.
)

if "%choice%"=="3" (
    echo.
    echo Testing current setup...
    echo.
    if exist .venv (
        call .venv\Scripts\activate
        python --version
        python -c "import tkinter; print('Tkinter: OK')"
        python -c "import fuzzywuzzy; print('FuzzyWuzzy: OK')"
        echo.
        echo Setup looks good! Ready to run.
        echo.
    ) else (
        echo Virtual environment not found. Run setup.bat first.
    )
)

echo Press any key to continue...
pause >nul
