@echo off
chcp 65001 >nul
echo ================================================
echo  Low Price Hunter - Installer
echo ================================================
echo.
echo If this is a source-only download (no software\ folder),
echo the installer will fetch the latest portable build from GitHub Releases.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\Install-LowPriceHunter.ps1" %*
set RC=%ERRORLEVEL%

echo.
if not "%RC%"=="0" (
  echo Installation failed ^(exit code %RC%^).
  echo.
  echo   * Make sure the whole folder was extracted ^(not run inside the zip^).
  echo   * If the auto-download failed, get the portable build manually:
  echo     https://github.com/a2003030700/LowPriceHunter/releases/latest
  echo.
) else (
  echo Done. A "Low Price Hunter" shortcut was created on your Desktop.
  echo.
)
pause
exit /b %RC%
