@echo off
rem Setup wizard. Works from a path with spaces and non-Latin characters:
rem every path is quoted and %~dp0 keeps its trailing backslash.
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
call "%~dp0_find-python.cmd" || (pause & exit /b 1)
set "PYTHONPATH=%~dp0src"
%PY% -m centauri_bot setup
echo.
pause
endlocal
