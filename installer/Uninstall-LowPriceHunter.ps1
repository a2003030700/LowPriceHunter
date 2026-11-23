$ErrorActionPreference = "SilentlyContinue"
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\LowPriceHunter"

Get-Process LowPriceHunter, LowPriceHunterHost | Stop-Process -Force

$desktop = [Environment]::GetFolderPath("Desktop")
Remove-Item (Join-Path $desktop "Low Price Hunter.lnk") -Force

$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
Remove-Item (Join-Path $startMenu "Low Price Hunter.lnk") -Force
Remove-Item (Join-Path $startMenu "Uninstall Low Price Hunter.lnk") -Force

Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LowPriceHunter" -Recurse -Force

if (Test-Path $InstallDir) {
    Remove-Item $InstallDir -Recurse -Force
}

Write-Host "Low Price Hunter has been uninstalled."
