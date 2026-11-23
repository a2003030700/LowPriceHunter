@echo off
chcp 65001 >nul
echo Uninstalling Low Price Hunter...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\Uninstall-LowPriceHunter.ps1"
pause
