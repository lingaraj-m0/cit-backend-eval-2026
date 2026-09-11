@echo off
call "%~dp0kit.cmd" -m processor %*
exit /b %errorlevel%
