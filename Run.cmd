@echo off
rem Starts the bot. Keep only one instance running: Telegram allows exactly
rem one long-polling consumer per token.
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
call "%~dp0_find-python.cmd" || (pause & exit /b 1)
set "PYTHONPATH=%~dp0src"
%PY% -m centauri_bot run
if errorlevel 1 (
    echo.
    echo   The bot stopped with an error. Run Setup.cmd if the configuration
    echo   is incomplete, or check the log:
    %PY% -m centauri_bot where
    echo.
    pause
)
endlocal
