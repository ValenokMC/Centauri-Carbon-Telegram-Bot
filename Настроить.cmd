@echo off
rem Russian-named shortcut to Setup.cmd. Nothing but a wrapper: all the logic
rem lives in Python, and duplicating it in two batch files would guarantee
rem the two drift apart.
call "%~dp0Setup.cmd" %*
