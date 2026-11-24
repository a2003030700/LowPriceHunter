param(
    [string]$Version = "",
    [string]$OutDir = "",
    [switch]$KeepStaging
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $OutDir) { $OutDir = Join-Path $root "dist" }

if (-not $Version) {
    $initPath = Join-Path $root "ps5hunter\__init__.py"
    $init = Get-Content -Raw -Encoding UTF8 $initPath
    if ($init -match '__version__\s*=\s*"([^"]+)"') { $Version = $Matches[1] }
}
if (-not $Version) { throw "無法取得版本號，請用 -Version 指定" }

$tag = $Version.TrimStart("v", "V")
$verTag = "v$tag"

$appDir = Join-Path $root "dist\LowPriceHunter"
if (-not (Test-Path (Join-Path $appDir "LowPriceHunter.exe"))) {
    throw "找不到 dist\LowPriceHunter\LowPriceHunter.exe，請先建置（build.ps1 或 PyInstaller）"
}

New-Item -ItemType Directory -Force $OutDir | Out-Null

# ---- 隱私保護：打包前清掉 data/ 內的個人設定（只保留範例檔）----
$dataDir = Join-Path $appDir "data"
if (Test-Path $dataDir) {
    $allowed = @("bot_email.example.json", ".env.example")
    Get-ChildItem $dataDir -Force |
        Where-Object { $allowed -notcontains $_.Name } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

# ---- 可攜版：解壓後直接雙擊「一鍵安裝」即可，不需要另外下載 update ----
$stage = Join-Path $OutDir "LowPriceHunter-$verTag-win64"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $stage | Out-Null

foreach ($name in @("一鍵安裝 Low Price Hunter.bat", "一鍵卸載 Low Price Hunter.bat", "README.md", "LICENSE")) {
    $source = Join-Path $root $name
    if (Test-Path $source) { Copy-Item $source $stage -Force }
}
Copy-Item (Join-Path $root "installer") (Join-Path $stage "installer") -Recurse -Force

$softwareDir = Join-Path $stage "software\LowPriceHunter"
New-Item -ItemType Directory -Force $softwareDir | Out-Null
robocopy $appDir $softwareDir /E /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "複製程式檔失敗 (robocopy $LASTEXITCODE)" }

$portableZip = Join-Path $OutDir "LowPriceHunter-$verTag-win64.zip"
Remove-Item $portableZip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path $stage -DestinationPath $portableZip -CompressionLevel Optimal

# ---- 更新檔：給 App 內建更新器下載（扁平結構，直接覆蓋安裝目錄）----
$updateZip = Join-Path $OutDir "LowPriceHunter-update.zip"
Remove-Item $updateZip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $appDir "*") -DestinationPath $updateZip -CompressionLevel Optimal

if (-not $KeepStaging) { Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue }

Get-Item $portableZip, $updateZip | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } } | Format-Table -AutoSize
Write-Host "打包完成。上傳這兩個檔案到 GitHub Release 即可。"
exit 0
