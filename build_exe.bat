@echo off
setlocal
cd /d "%~dp0"

where pyinstaller >nul 2>nul
if errorlevel 1 (
    echo.
    echo PyInstaller was not found on PATH.
    echo Install requirements first:
    echo   python -m pip install -r "%~dp0requirements.txt"
    pause
    exit /b 1
)

echo Using PyInstaller from PATH:
where pyinstaller
pyinstaller --version

echo.
echo Building "EPUB Fixer and Converter.exe" from EPUB_GUI.spec...
pyinstaller --clean --noconfirm "%~dp0EPUB_GUI.spec"
if errorlevel 1 (
    echo.
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo Build complete: "%~dp0dist\EPUB Fixer and Converter.exe"
pause
