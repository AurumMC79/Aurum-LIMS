@echo off
rem Starts Aurum LIMS. The first run sets up the Python environment (needs internet once).
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\pythonw.exe" (
    echo Setting up the Aurum LIMS Python environment. This only happens once...
    py -3 -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :error
    ".venv\Scripts\python.exe" -m pip install -e ".[dev]" || goto :error
)

start "" ".venv\Scripts\pythonw.exe" -m aurum_lims
exit /b 0

:error
echo.
echo Setup failed. Make sure Python 3.11 or newer is installed from python.org.
pause
exit /b 1
