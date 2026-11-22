@echo off
setlocal DisableDelayedExpansion
if not defined SHOREKEEPER_CORE_PYTHON set "SHOREKEEPER_CORE_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%SHOREKEEPER_CORE_PYTHON%" (
    echo Core Python is missing. In the project folder, run uv sync --locked.
    echo For another core environment, set SHOREKEEPER_CORE_PYTHON to its full Python path.
    pause
    exit /b 1
)
"%SHOREKEEPER_CORE_PYTHON%" -I -B -X utf8 "%~dp0tools\desktop_launcher\launch.py"
set "SHOREKEEPER_LAUNCH_RESULT=%errorlevel%"
if "%SHOREKEEPER_LAUNCH_RESULT%"=="130" exit /b 130
if not "%SHOREKEEPER_LAUNCH_RESULT%"=="0" pause
exit /b %SHOREKEEPER_LAUNCH_RESULT%
