@echo off
echo ============================================
echo  Circuit Tools - Setup and Run
echo ============================================
echo.

:: Find Python - try py launcher first, then python, then python3
set PYTHON=
where py >nul 2>&1 && set PYTHON=py
if "%PYTHON%"=="" where python >nul 2>&1 && set PYTHON=python
if "%PYTHON%"=="" where python3 >nul 2>&1 && set PYTHON=python3

if "%PYTHON%"=="" (
    echo ERROR: Python not found. Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

echo Using: %PYTHON%
%PYTHON% --version
echo.

:: Check if PyQt6 is installed
%PYTHON% -c "import PyQt6" >nul 2>&1
if %errorlevel% neq 0 (
    echo PyQt6 not found. Installing dependencies...
    echo.
    %PYTHON% -m pip install --upgrade pip
    %PYTHON% -m pip install PyQt6 PyQt6-Qt6 pyqtgraph numpy pandas scipy
    if %errorlevel% neq 0 (
        echo.
        echo ERROR: Installation failed. Try running as Administrator.
        pause
        exit /b 1
    )
    echo.
    echo Dependencies installed successfully.
) else (
    echo Dependencies already installed.
)

echo.
echo Starting Circuit Tools...
echo.

:: Run from the script's directory
cd /d "%~dp0"
%PYTHON% main.py %*

if %errorlevel% neq 0 (
    echo.
    echo Application exited with an error.
    pause
)
