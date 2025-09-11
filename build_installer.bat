@echo off
echo ========================================
echo   Building MSTS Consist Editor Installer
echo ========================================
echo.

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.8+ from https://python.org
    pause
    exit /b 1
)

REM Check if PyInstaller is installed
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo ERROR: Failed to install PyInstaller
        pause
        exit /b 1
    )
)

echo Building standalone executable...
echo This may take several minutes...
echo.

REM Build the executable
python -m PyInstaller --clean --noconfirm MSTS_Consist_Editor.spec

if errorlevel 1 (
    echo.
    echo ERROR: Build failed!
    echo Check the error messages above for details.
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Build Complete!
echo ========================================
echo.
echo The executable has been created in the 'dist' folder:
echo   dist\MSTS_Consist_Editor.exe
echo.
echo You can now distribute this single .exe file to other computers.
echo No Python installation or setup required on target computers!
echo.
echo To create a zip package with the executable:
echo   - Copy dist\MSTS_Consist_Editor.exe
echo   - Add README.md and any sample data files you want to include
echo   - Zip everything together
echo.
pause
