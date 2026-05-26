@echo off
setlocal
cd /d "%~dp0"

set "PY_CMD=python"
where py >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3"

%PY_CMD% -c "import PySide6, ebooklib, bs4, lxml, PIL" >nul 2>nul
if errorlevel 1 (
    echo Installing required Python packages...
    %PY_CMD% -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo.
        echo Failed to install requirements.
        pause
        exit /b 1
    )
)

%PY_CMD% "%~dp0EPUB_GUI.py"
if errorlevel 1 (
    echo.
    echo EPUB GUI exited with an error.
    pause
    exit /b 1
)
