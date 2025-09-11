@echo off
echo ========================================
echo   MSTS Consist Editor Setup
echo ========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.8+ from https://python.org
    pause
    exit /b 1
)

echo Python found. Setting up virtual environment...

REM Create virtual environment
if not exist .venv (
    python -m venv .venv
    echo Virtual environment created.
) else (
    echo Virtual environment already exists.
)

REM Activate virtual environment
call .venv\Scripts\activate

REM Upgrade pip
python -m pip install --upgrade pip

REM Install requirements
if exist requirements.txt (
    echo Installing dependencies from requirements.txt...
    pip install -r requirements.txt
    echo Dependencies installed successfully.
) else (
    echo WARNING: requirements.txt not found. Installing basic dependencies...
    pip install tk fuzzywuzzy python-levenshtein
)

echo.
echo ========================================
echo   Setup Complete!
echo ========================================
echo.
echo To run the application:
echo 1. Double-click 'run_consist_editor.bat'
echo 2. Or run: .venv\Scripts\activate && python msts_consist_editor_gui.py
echo.
pause
