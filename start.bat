@echo off
echo Starting OpenCode Session Manager...

REM Try py launcher first (Python for Windows), then python
where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
    py -3 "%~dp0run.py" %*
    goto :done
)

where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    python "%~dp0run.py" %*
    goto :done
)

REM Check if it's the Microsoft Store stub
python --version 2>&1 | findstr /i "Python 3" >nul
if %ERRORLEVEL% equ 0 (
    python "%~dp0run.py" %*
    goto :done
)

echo.
echo Error! Python 3.10+ not found.
echo.
echo Install Python from: https://www.python.org/downloads/
echo Or use: winget install Python.Python.3.13
echo.
pause
exit /b 1

:done
if %ERRORLEVEL% neq 0 (
    echo.
    echo Error occurred during execution.
    pause
)
