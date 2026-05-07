@echo off
setlocal
cd /d "%~dp0"

echo ===============================================
echo   BOR scheduling web app
echo ===============================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_CMD=py"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PYTHON_CMD=python"
    ) else (
        echo Python was not found.
        echo Please install Python 3.10 or newer, then run this file again.
        echo Download: https://www.python.org/downloads/
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating local virtual environment...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo Installing/updating required packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install required packages.
    pause
    exit /b 1
)

echo.
echo Starting website...
echo Browser URL: http://localhost:8501
echo Close this window to stop the website.
echo.
".venv\Scripts\python.exe" -m streamlit run streamlit_app.py --server.address localhost --server.port 8501 --server.headless true --browser.gatherUsageStats false

pause
