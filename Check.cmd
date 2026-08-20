@echo off
rem Read-only diagnosis: shows the configuration (without the token) and
rem probes the printer ports. Changes nothing.
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
call "%~dp0_find-python.cmd" || (pause & exit /b 1)
set "PYTHONPATH=%~dp0src"
%PY% -m centauri_bot check
echo.
pause
endlocal
