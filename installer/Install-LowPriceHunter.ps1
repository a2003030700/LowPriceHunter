param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\LowPriceHunter"),
    [switch]$NoLaunch,
    [string]$Repo = "a2003030700/LowPriceHunter",
    [switch]$NoDownload
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "software\LowPriceHunter"
$exeName = "LowPriceHunter.exe"

function Find-Exe([string]$dir) {
    if (Test-Path (Join-Path $dir $exeName)) { return (Join-Path $dir $exeName) }
    $hit = Get-ChildItem $dir -Recurse -Filter $exeName -File -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($hit) { return $hit.FullName }
    return $null
}

function Get-PortableFromRelease {
    param([string]$Repo, [string]$Dest)

    Write-Host "  Repo 內沒有執行檔，改從 GitHub Releases 下載最新可攜版..."
    $headers = @{
        "User-Agent" = "LowPriceHunter-Installer/1.0"
        "Accept"     = "application/vnd.github+json"
    }
    try {
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
            -Headers $headers -TimeoutSec 30
    } catch {
        throw "無法查詢 GitHub Releases（$Repo）：$($_.Exception.Message)"
    }

    $candidates = @($release.assets | Where-Object { $_.name -like "*.zip" })
    if ($candidates.Count -eq 0) {
        throw "Release $($release.tag_name) 沒有任何 zip 檔，請改用手動下載：$($release.html_url)"
    }
    # 優先完整可攜版，其次 update 包
    $zip = $candidates | Sort-Object @{ Expression = {
            if ($_.name -match "portable|win64|win") { 0 }
            elseif ($_.name -match "update") { 1 }
            else { 2 }
        } } | Select-Object -First 1

    $tmp = Join-Path $env:TEMP ("lph_" + [guid]::NewGuid().ToString("N") + ".zip")
    Write-Host "  下載 $($zip.name)（$([math]::Round($zip.size / 1MB, 1)) MB）..."
    try {
        Invoke-WebRequest -Uri $zip.browser_download_url -OutFile $tmp -UseBasicParsing -TimeoutSec 600
        New-Item -ItemType Directory -Force -Path $Dest | Out-Null
        Expand-Archive -Path $tmp -DestinationPath $Dest -Force
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }

    # 解法一：解壓後多一層資料夾時，把內容往上搬
    $found = Find-Exe $Dest
    if ($found) {
        $foundDir = Split-Path -Parent $found
        if ($foundDir -ne (Resolve-Path $Dest).Path) {
            Get-ChildItem $foundDir -Force | Move-Item -Destination $Dest -Force
        }
    }
    if (-not (Find-Exe $Dest)) {
        throw "下載的壓縮檔內找不到 $exeName，請改用手動下載：$($release.html_url)"
    }
    Write-Host "  已取得 $($release.tag_name)"
}

Write-Host "Installing Low Price Hunter"

# 優先使用 repo 內附的可攜版 zip（Code → Download ZIP 也能離線安裝）
if (-not (Find-Exe $src)) {
    $bundledZip = Join-Path $root "software\LowPriceHunter.zip"
    if (Test-Path $bundledZip) {
        Write-Host "  解壓內附的 software\LowPriceHunter.zip ..."
        New-Item -ItemType Directory -Force -Path $src | Out-Null
        Expand-Archive -Path $bundledZip -DestinationPath $src -Force
        $hit = Find-Exe $src
        if ($hit -and (Split-Path -Parent $hit) -ne (Resolve-Path $src).Path) {
            Get-ChildItem (Split-Path -Parent $hit) -Force | Move-Item -Destination $src -Force
        }
    }
}

if (-not (Find-Exe $src)) {
    if ($NoDownload) {
        throw "找不到 software\LowPriceHunter\$exeName，也沒有內附的 software\LowPriceHunter.zip。請下載可攜版（GitHub Releases）。"
    }
    Get-PortableFromRelease -Repo $Repo -Dest $src
}

$exe = Find-Exe $src
if (-not $exe) { throw "安裝失敗：找不到 $exeName" }
# 若 exe 在子資料夾，把 $src 指向它所在目錄
$src = Split-Path -Parent $exe

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

$exe = Join-Path $InstallDir $exeName
if (-not (Test-Path $exe)) { throw "Install failed: $exeName not found" }

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
    Set-ItemProperty $reg -Name DisplayVersion -Value "1.4.0"
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
