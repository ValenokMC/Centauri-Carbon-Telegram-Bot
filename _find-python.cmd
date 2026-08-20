@echo off
rem ---------------------------------------------------------------------------
rem Locates a usable Python and leaves it in %PY%. Included by the other
rem launchers; not meant to be run on its own.
rem
rem Order matters: the py launcher understands "-3" and picks the newest
rem interpreter, which is what a Windows user most likely wants. Bare "python"
rem on a machine without Python opens the Microsoft Store instead of failing,
rem so it is checked with a real version call, not just "where".
rem ---------------------------------------------------------------------------
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY python3 -c "import sys" >nul 2>&1 && set "PY=python3"
if not defined PY (
    echo.
    echo   Python not found.
    echo.
    echo   Install Python 3.9 or newer from https://www.python.org/downloads/
    echo   During installation tick "Add python.exe to PATH".
    echo.
    echo   Then run this file again.
    echo.
    exit /b 1
)
exit /b 0
