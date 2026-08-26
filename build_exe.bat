@echo off
setlocal
cd /d "%~dp0"

echo ===============================================
echo   Build BOR_Scheduler.exe
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
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating local virtual environment...
    %PYTHON_CMD% -m venv .venv
)

echo Installing/updating required packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller

echo.
echo Building BOR_Scheduler.exe (this can take a few minutes)...
".venv\Scripts\python.exe" -m PyInstaller BOR_Scheduler.spec --noconfirm

echo.
echo Done. The executable is at dist\BOR_Scheduler.exe
pause
