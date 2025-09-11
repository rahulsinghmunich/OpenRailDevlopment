@echo off
REM MSTS Consist Editor - Windows Batch Launcher
REM This batch file provides easy access to both GUI and CLI versions

title MSTS Consist Editor - TSRE5 Style

echo.
echo ╔══════════════════════════════════════════════════════════════════════════════╗
echo ║                    MSTS Consist Editor - TSRE5 Style                     ║
echo ║                          Windows Batch Launcher                          ║
echo ╚══════════════════════════════════════════════════════════════════════════════╝
echo.

REM Check if virtual environment exists and activate it
if exist ".venv\Scripts\activate.bat" (
    echo Activating virtual environment...
    call .venv\Scripts\activate.bat
    if errorlevel 1 (
        echo WARNING: Failed to activate virtual environment
        echo Falling back to system Python
        echo.
    ) else (
        echo Virtual environment activated successfully
        echo.
    )
) else (
    echo WARNING: Virtual environment not found at .venv
    echo Using system Python installation
    echo.
)

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.6 or later from https://www.python.org/
    echo.
    pause
    exit /b 1
)

REM Check if consistEditor.py exists
if not exist "consistEditor.py" (
    echo ERROR: consistEditor.py not found in current directory
    echo Please ensure the resolver script is available
    echo.
    pause
    exit /b 1
)

REM Main menu
:menu
cls
echo.
echo ╔══════════════════════════════════════════════════════════════════════════════╗
echo ║                    MSTS Consist Editor - TSRE5 Style                     ║
echo ║                          Windows Batch Launcher                          ║
echo ╚══════════════════════════════════════════════════════════════════════════════╝
echo.
echo Select your preferred interface:
echo.
echo   1. GUI Mode (Graphical Interface - Recommended)
echo   2. CLI Mode (Command Line Interface)
echo   3. Quick Single File Processing
echo   4. Quick Batch Processing
echo   5. Help and Examples
echo   6. Exit
echo.
set /p choice="Enter your choice [1-6]: "

if "%choice%"=="1" goto gui_mode
if "%choice%"=="2" goto cli_mode  
if "%choice%"=="3" goto quick_single
if "%choice%"=="4" goto quick_batch
if "%choice%"=="5" goto help
if "%choice%"=="6" goto exit
goto invalid_choice

:gui_mode
echo.
@echo off
echo Starting MSTS Consist Editor...

REM Check if virtual environment exists
if not exist .venv (
    echo Virtual environment not found. Running setup...
    call setup.bat
    if errorlevel 1 (
        echo Setup failed. Please run setup.bat manually.
        pause
        exit /b 1
    )
)

REM Activate virtual environment
call .venv\Scripts\activate

REM Check if GUI file exists
if not exist msts_consist_editor_gui.py (
    echo ERROR: msts_consist_editor_gui.py not found in current directory
    echo Please ensure all files are in the same directory
    pause
    exit /b 1
)

REM Run the GUI application
echo Launching MSTS Consist Editor GUI...
python msts_consist_editor_gui.py

REM Keep window open if there's an error
if errorlevel 1 (
    echo.
    echo Application exited with error code %errorlevel%
    echo Press any key to continue...
    pause
)

:cli_mode
echo.
echo Starting CLI Mode...
echo ────────────────────────────────────────────────────────────────────────────
if exist "msts_consist_cli.py" (
    python msts_consist_cli.py
) else (
    echo ERROR: msts_consist_cli.py not found
    echo Please ensure all required files are present
    pause
)
goto menu

:quick_single
echo.
echo Quick Single File Processing
echo ────────────────────────────────────────────────────────────────────────────
set /p consist_file="Enter consist file path (.con): "
if "%consist_file%"=="" goto menu

if exist "msts_consist_cli.py" (
    python msts_consist_cli.py --file "%consist_file%"
) else (
    echo ERROR: msts_consist_cli.py not found
    pause
)
goto menu

:quick_batch
echo.
echo Quick Batch Processing
echo ────────────────────────────────────────────────────────────────────────────
set /p consists_dir="Enter consists directory: "
if "%consists_dir%"=="" goto menu

set /p trainset_dir="Enter trainset directory: "
if "%trainset_dir%"=="" goto menu

echo.
echo Options:
set /p dry_run="Dry run only (preview changes)? [Y/n]: "
set /p explain="Show detailed explanations? [y/N]: "

set cmd_args=--batch --consists-dir "%consists_dir%" --trainset-dir "%trainset_dir%"

if not "%dry_run%"=="n" if not "%dry_run%"=="N" (
    set cmd_args=%cmd_args% --dry-run
)

if "%explain%"=="y" if "%explain%"=="Y" (
    set cmd_args=%cmd_args% --explain
)

echo.
echo Running: python msts_consist_cli.py %cmd_args%
echo ────────────────────────────────────────────────────────────────────────────

if exist "msts_consist_cli.py" (
    python msts_consist_cli.py %cmd_args%
) else (
    echo ERROR: msts_consist_cli.py not found
    pause
)
goto menu

:help
cls
echo.
echo ╔══════════════════════════════════════════════════════════════════════════════╗
echo ║                              HELP & EXAMPLES                                ║
echo ╚══════════════════════════════════════════════════════════════════════════════╝
echo.
echo USAGE EXAMPLES:
echo.
echo GUI Mode (Recommended):
echo   • Launch the graphical interface for easy point-and-click operation
echo   • Similar to TSRE5 interface with file browsers and status display
echo   • Best for interactive use and visual feedback
echo.
echo CLI Mode:
echo   • Interactive command-line interface with menus
echo   • Good for terminal users and advanced operations
echo   • Provides detailed analysis and batch processing
echo.
echo Quick Processing:
echo   • Single File: Process one consist file quickly
echo   • Batch: Process entire directories automatically
echo   • Good for automation and scripting
echo.
echo DIRECT COMMAND LINE USAGE:
echo.
echo   python consistEditor.py [consists_dir] [trainset_dir] [options]
echo.
echo   Options:
echo     --dry-run    Preview changes without modifying files
echo     --explain    Show detailed resolution information  
echo     --debug      Enable verbose debugging output
echo.
echo REQUIREMENTS:
echo   • Python 3.6 or later
echo   • consistEditor.py (the main resolver script)
echo   • Valid MSTS/OpenRails installation
echo   • Trainset assets in correct directory structure
echo.
echo WORKFLOW:
echo   1. Select consists directory (containing .con files)
echo   2. Select trainset directory (containing asset folders)  
echo   3. Choose processing options (dry-run, explain, debug)
echo   4. Run the resolver to fix missing assets
echo   5. Review results and apply changes
echo.
pause
goto menu

:invalid_choice
echo.
echo Invalid choice. Please select 1-6.
echo.
pause
goto menu

:exit
echo.
echo Thank you for using MSTS Consist Editor!
echo.
pause
exit /b 0