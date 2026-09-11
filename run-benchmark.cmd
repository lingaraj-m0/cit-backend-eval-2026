@echo off
call "%~dp0kit.cmd" -m tools.suite %*
exit /b %errorlevel%
