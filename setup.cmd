@echo off
setlocal
pushd "%~dp0" || exit /b 1
where py >nul 2>nul
if errorlevel 1 (
    python bootstrap.py
) else (
    py -3 bootstrap.py
)
set "result=%errorlevel%"
popd
exit /b %result%
