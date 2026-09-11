@echo off
call "%~dp0kit.cmd" -m pytest %*
exit /b %errorlevel%
