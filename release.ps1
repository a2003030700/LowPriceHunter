param(
    [switch]$SkipBuild,
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not $Version) {
    $init = Get-Content -Raw -Encoding UTF8 (Join-Path $root "ps5hunter\__init__.py")
    if ($init -match '__version__\s*=\s*"([^"]+)"') { $Version = $Matches[1] }
}
if (-not $Version) { throw "無法取得版本號" }

function Invoke-PsScript([string]$path, [string[]]$extra = @()) {
    $argv = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $path) + $extra
    & powershell @argv | Out-Host
    return $LASTEXITCODE
}

Write-Host "=== 1/4 建置 ===" -ForegroundColor Cyan
if ($SkipBuild) {
    Write-Host "  已指定 -SkipBuild，使用現有 dist"
    if (-not (Test-Path (Join-Path $root "dist\LowPriceHunter\LowPriceHunter.exe"))) {
        throw "dist 內沒有 LowPriceHunter.exe，不能跳過建置"
    }
} else {
    if ((Invoke-PsScript (Join-Path $root "build.ps1")) -ne 0) { throw "建置失敗" }
}

Write-Host "=== 2/4 煙霧測試 ===" -ForegroundColor Cyan
if ((Invoke-PsScript (Join-Path $root "smoke.ps1")) -ne 0) { throw "煙霧測試失敗，中止發布" }

Write-Host "=== 3/4 打包 ===" -ForegroundColor Cyan
if ((Invoke-PsScript (Join-Path $root "package.ps1") @("-Version", $Version)) -ne 0) { throw "打包失敗" }

Write-Host "=== 4/4 隱私掃描 ===" -ForegroundColor Cyan
Add-Type -AssemblyName System.IO.Compression.FileSystem
$pattern = 'data/(app_config|bot_email\.(json|dpapi))|ui_profile|webview_profile|\.db$'
$bad = @()
foreach ($zip in Get-ChildItem (Join-Path $root "dist\*.zip")) {
    $archive = [System.IO.Compression.ZipFile]::OpenRead($zip.FullName)
    try {
        $hits = @($archive.Entries | Where-Object { $_.FullName -match $pattern })
    } finally {
        $archive.Dispose()
    }
    if ($hits.Count -gt 0) { $bad += "$($zip.Name) -> $($hits[0].FullName)" }
}
if ($bad.Count -gt 0) {
    $bad | ForEach-Object { Write-Host "  [FAIL] 含個人資料：$_" -ForegroundColor Red }
    throw "打包內容含個人設定，中止發布"
}
Write-Host "  [PASS] 兩個 zip 都沒有個人設定" -ForegroundColor Green

Write-Host ""
Write-Host "READY $Version" -ForegroundColor Green
Get-ChildItem (Join-Path $root "dist\*.zip") |
    Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } } |
    Format-Table -AutoSize
Write-Host "下一步：把 software\LowPriceHunter.zip 換成 LowPriceHunter-*-win64.zip 後 commit，或上傳到 GitHub Release。"
exit 0
