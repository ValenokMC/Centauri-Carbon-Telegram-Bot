@echo off
rem Registers a per-user logon task. Shows exactly what it will create and
rem asks first. No administrator rights needed.
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
call "%~dp0_find-python.cmd" || (pause & exit /b 1)
set "PYTHONPATH=%~dp0src"
%PY% -m centauri_bot autostart
echo.
pause
endlocal
