@echo off
REM Steno - a modern annalist for your meetings - an automaton stenographer.
REM Zero-Python double-click & CLI launcher for Windows 10/11.
setlocal
set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
set "STENO_DIR=%~dp0"
set "STENO_PY=%STENO_DIR%steno.py"

if not exist "%STENO_PY%" (
    set "STENO_DIR=%LOCALAPPDATA%\Steno\"
    set "STENO_PY=%LOCALAPPDATA%\Steno\steno.py"
)

if not exist "%STENO_PY%" (
    echo Downloading steno.py...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "New-Item -ItemType Directory -Force -Path '%LOCALAPPDATA%\Steno' | Out-Null; irm https://hypertekst.net/steno/steno.py -OutFile '%LOCALAPPDATA%\Steno\steno.py'"
)

where uv >nul 2>nul || (
    echo Installing uv ^(standalone Python runtime^)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
)

uv run --python 3.12 --script "%STENO_PY%" %*
exit /b %ERRORLEVEL%
