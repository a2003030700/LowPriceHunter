@echo off
chcp 65001 >nul
echo Installing Low Price Hunter...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\Install-LowPriceHunter.ps1"
if errorlevel 1 (
  echo.
  echo Installation failed. Please extract the whole zip and make sure the software folder exists.
  pause
)
