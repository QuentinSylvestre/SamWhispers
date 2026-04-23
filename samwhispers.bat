@echo off
REM Launcher for SamWhispers (Windows).
REM Activates the project venv and forwards all arguments.

setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV=%SCRIPT_DIR%.venv"

if not exist "%VENV%\Scripts\python.exe" (
    echo Error: venv not found at %VENV% >&2
    echo Run: python -m venv .venv ^&^& .venv\Scripts\pip install -e ".[dev]" >&2
    exit /b 1
)

"%VENV%\Scripts\python.exe" -m samwhispers %*
