@echo off
rem Removes the logon task created by Install-Autostart.cmd.
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
call "%~dp0_find-python.cmd" || (pause & exit /b 1)
set "PYTHONPATH=%~dp0src"
%PY% -m centauri_bot autostart remove
echo.
pause
endlocal
