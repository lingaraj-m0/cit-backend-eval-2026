@echo off
setlocal
pushd "%~dp0" || exit /b 1
if not exist ".venv\Scripts\python.exe" (
    echo Run setup.cmd first.
    popd
    exit /b 1
)
".venv\Scripts\python.exe" %*
set "result=%errorlevel%"
popd
exit /b %result%
