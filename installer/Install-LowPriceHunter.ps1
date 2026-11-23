param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\LowPriceHunter"),
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "software\LowPriceHunter"

if (-not (Test-Path (Join-Path $src "LowPriceHunter.exe"))) {
    throw "Cannot find software\LowPriceHunter\LowPriceHunter.exe. Extract the zip completely first."
}

Write-Host "Installing Low Price Hunter"
Write-Host "  From: $src"
Write-Host "  To  : $InstallDir"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# Keep existing data (login session, config, database) on upgrade
$dataBackup = $null
if (Test-Path (Join-Path $InstallDir "data")) {
    $dataBackup = Join-Path $env:TEMP ("lph_data_" + [guid]::NewGuid().ToString("N"))
    Copy-Item (Join-Path $InstallDir "data") $dataBackup -Recurse -Force
}

robocopy $src $InstallDir /E /R:1 /W:1 /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Copy failed (robocopy $LASTEXITCODE)" }

if ($dataBackup) {
    Remove-Item (Join-Path $InstallDir "data") -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item $dataBackup (Join-Path $InstallDir "data") -Recurse -Force
    Remove-Item $dataBackup -Recurse -Force -ErrorAction SilentlyContinue
}

$exe = Join-Path $InstallDir "LowPriceHunter.exe"
if (-not (Test-Path $exe)) { throw "Install failed: LowPriceHunter.exe not found" }

# Desktop and Start Menu shortcuts
$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = $ws.CreateShortcut((Join-Path $desktop "Low Price Hunter.lnk"))
$lnk.TargetPath = $exe
$lnk.WorkingDirectory = $InstallDir
$lnk.IconLocation = "$exe,0"
$lnk.Description = "Low Price Hunter"
$lnk.Save()

$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$lnk2 = $ws.CreateShortcut((Join-Path $startMenu "Low Price Hunter.lnk"))
$lnk2.TargetPath = $exe
$lnk2.WorkingDirectory = $InstallDir
$lnk2.IconLocation = "$exe,0"
$lnk2.Save()

# Uninstaller
$uninstallCopy = Join-Path $InstallDir "Uninstall-LowPriceHunter.ps1"
Copy-Item (Join-Path $PSScriptRoot "Uninstall-LowPriceHunter.ps1") $uninstallCopy -Force
$lnk3 = $ws.CreateShortcut((Join-Path $startMenu "Uninstall Low Price Hunter.lnk"))
$lnk3.TargetPath = "powershell.exe"
$lnk3.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$uninstallCopy`""
$lnk3.IconLocation = "$exe,0"
$lnk3.Save()

# Add/Remove Programs entry (optional; keep installing even if denied)
try {
    $reg = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LowPriceHunter"
    New-Item $reg -Force | Out-Null
    Set-ItemProperty $reg -Name DisplayName -Value "Low Price Hunter"
    Set-ItemProperty $reg -Name DisplayVersion -Value "1.0.0"
    Set-ItemProperty $reg -Name Publisher -Value "Low Price Hunter"
    Set-ItemProperty $reg -Name InstallLocation -Value $InstallDir
    Set-ItemProperty $reg -Name DisplayIcon -Value $exe
    Set-ItemProperty $reg -Name UninstallString -Value "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$uninstallCopy`""
    Set-ItemProperty $reg -Name NoModify -Value 1 -Type DWord
    Set-ItemProperty $reg -Name NoRepair -Value 1 -Type DWord
} catch {
    Write-Warning "Could not write Add/Remove Programs entry: $($_.Exception.Message)"
}

Write-Host "Installed: $exe"
if (-not $NoLaunch) {
    Write-Host "Starting Low Price Hunter..."
    Start-Process $exe
}
