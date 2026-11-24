@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem First run from source: create venv and install dependencies automatically
if not exist ".venv\Scripts\python.exe" (
  echo First run detected: creating .venv and installing dependencies...
  python -m venv .venv
  if errorlevel 1 goto :fail
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto :fail
  echo Setup complete.
  echo.
)

".venv\Scripts\python.exe" start_ui.py
pause
exit /b 0

:fail
echo.
echo Failed to set up the Python environment.
echo   * Install Python 3.10+ from https://www.python.org/downloads/
echo   * Make sure "Add python.exe to PATH" was checked during install.
pause
exit /b 1
