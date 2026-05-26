@echo off
setlocal
cd /d "%~dp0"

set "PY_CMD=python"
where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 --version >nul 2>nul
    if not errorlevel 1 set "PY_CMD=py -3.12"
)

echo Using Python command: %PY_CMD%
%PY_CMD% --version

echo Installing build requirements...
%PY_CMD% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo Failed to install requirements.
    pause
    exit /b 1
)

echo.
echo Building EPUB_GUI.exe from EPUB_GUI.spec...
%PY_CMD% -m PyInstaller --clean --noconfirm "%~dp0EPUB_GUI.spec"
if errorlevel 1 (
    echo.
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo Build complete: "%~dp0dist\EPUB_GUI.exe"
pause
