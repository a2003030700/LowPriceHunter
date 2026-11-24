$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "[1/5] Python venv"
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
$py = Join-Path $root ".venv\Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements.txt
& $py -m pip install pyinstaller

Write-Host "[2/5] WebView2 SDK"
$native = Join-Path $root "native_host"
$need = @("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll")
$missing = @($need | Where-Object { -not (Test-Path (Join-Path $native $_)) })
if ($missing.Count -gt 0) {
    Write-Host "  缺少 $($missing -join ', ')，從 NuGet 下載 Microsoft.Web.WebView2..."
    $tmp = Join-Path $env:TEMP ("wv2_" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    try {
        $index = Invoke-RestMethod "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/index.json"
        $ver = @($index.versions | Where-Object { $_ -notmatch "-" })[-1]
        $url = "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$ver/microsoft.web.webview2.$ver.nupkg"
        $nupkg = Join-Path $tmp "wv2.zip"
        Invoke-WebRequest -Uri $url -OutFile $nupkg -UseBasicParsing
        Expand-Archive -Path $nupkg -DestinationPath $tmp -Force

        $lib = @("net462", "net45") | ForEach-Object { Join-Path $tmp "lib\$_" } |
            Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $lib) { throw "NuGet 套件內找不到 lib\net462 或 lib\net45" }
        Copy-Item (Join-Path $lib "*.dll") $native -Force
        $loader = Get-ChildItem $tmp -Recurse -Filter "WebView2Loader.dll" -File |
            Where-Object { $_.FullName -match "win-x64" } | Select-Object -First 1
        if ($loader) { Copy-Item $loader.FullName $native -Force }
        Write-Host "  已取得 WebView2 SDK $ver"
    } finally {
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "[3/5] Build native WebView2 host"
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

Write-Host "[4/5] PyInstaller"
& $py -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name LowPriceHunter `
    --icon "assets\logo.ico" `
    --add-data "ps5hunter\ui\index.html;ps5hunter\ui" `
    --add-data "assets;assets" `
    --add-data "native_host;native_host" `
    --collect-all playwright `
    start_ui.py

Write-Host "[5/5] 打包可攜版與更新檔"
$outDir = Join-Path $root "dist\LowPriceHunter"
if (-not (Test-Path $outDir)) { throw "PyInstaller 沒有產生 dist\LowPriceHunter" }

$example = Join-Path $root ".env.example"
if (Test-Path $example) {
    New-Item -ItemType Directory -Force (Join-Path $outDir "data") | Out-Null
    Copy-Item $example (Join-Path $outDir "data") -Force
} else {
    Write-Warning "找不到 .env.example，略過"
}

& (Join-Path $root "package.ps1")
