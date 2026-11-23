$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "[1/4] Python venv"
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
$py = Join-Path $root ".venv\Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements.txt
& $py -m pip install pyinstaller

Write-Host "[2/4] Build native WebView2 host"
$native = Join-Path $root "native_host"
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) {
    throw "找不到 csc.exe，請確認已安裝 .NET Framework 4.x"
}
& $csc /nologo /target:winexe "/out:$native\LowPriceHunterHost.exe" `
    /r:System.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll `
    "/r:$native\Microsoft.Web.WebView2.Core.dll" `
    "/r:$native\Microsoft.Web.WebView2.WinForms.dll" `
    (Join-Path $native "Host.cs")
if ($LASTEXITCODE -ne 0) { throw "原生視窗編譯失敗" }

Write-Host "[3/4] PyInstaller"
& $py -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name LowPriceHunter `
    --icon "assets\logo.ico" `
    --add-data "ps5hunter\ui\index.html;ps5hunter\ui" `
    --add-data "assets;assets" `
    --add-data "native_host;native_host" `
    --collect-all playwright `
    start_ui.py

Write-Host "[4/4] 加入範例設定"
$outData = Join-Path $root "dist\LowPriceHunter\data"
New-Item -ItemType Directory -Force $outData | Out-Null
$example = Join-Path $root "software\LowPriceHunter\data\bot_email.example.json"
if (Test-Path $example) {
    Copy-Item $example $outData -Force
}
Copy-Item (Join-Path $root ".env.example") $outData -Force

Write-Host "完成：dist\LowPriceHunter\LowPriceHunter.exe"
