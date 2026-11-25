param(
    [string]$ExePath = "",
    [int]$Port = 8765,
    [int]$TimeoutSec = 40
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ExePath) { $ExePath = Join-Path $root "dist\LowPriceHunter\LowPriceHunter.exe" }
if (-not (Test-Path $ExePath)) {
    Write-Host "找不到 $ExePath，請先執行 build.ps1" -ForegroundColor Red
    exit 1
}

$failed = @()
function Check([string]$name, [bool]$ok, [string]$detail = "") {
    if ($ok) {
        Write-Host "  [PASS] $name" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $name $detail" -ForegroundColor Red
        $script:failed += $name
    }
}

Write-Host "Smoke test: $ExePath"

$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
Check "連接埠 $Port 未被佔用" (-not $busy) "（目前由 PID $($busy.OwningProcess) 使用）"
if ($busy) {
    Write-Host "請先關閉佔用 $Port 的程式再測試。" -ForegroundColor Yellow
    exit 1
}

$env:PS5_UI_NO_BROWSER = "1"
$proc = Start-Process -FilePath $ExePath -PassThru
$base = "http://127.0.0.1:$Port"
$resp = $null

try {
    $up = $false
    for ($i = 0; $i -lt $TimeoutSec; $i++) {
        Start-Sleep -Seconds 1
        try {
            $resp = Invoke-WebRequest "$base/" -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -eq 200) { $up = $true; break }
        } catch { }
        if ($proc.HasExited) { break }
    }
    Check "啟動後 $TimeoutSec 秒內提供 UI" $up

    if ($up) {
        $html = [string]$resp.Content
        Check "首頁含核心函式 renderGroups" ($html -like "*renderGroups*")
        Check "首頁含一鍵更新介面 btn-update-apply" ($html -like "*btn-update-apply*")

        try {
            $state = Invoke-RestMethod "$base/api/state" -TimeoutSec 5
            Check "/api/state 回應正常" ($null -ne $state -and $null -ne $state.config)
        } catch {
            Check "/api/state 回應正常" $false $_.Exception.Message
        }

        try {
            $tray = Invoke-WebRequest "$base/api/tray-state" -UseBasicParsing -TimeoutSec 5
            Check "/api/tray-state 回應正常" ($tray.StatusCode -eq 200 -and ([string]$tray.Content -like "*monitoring=*"))
        } catch {
            Check "/api/tray-state 回應正常" $false $_.Exception.Message
        }
    }
} finally {
    & taskkill /T /F /PID $proc.Id *> $null
    Get-Process LowPriceHunterHost -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}

if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host "SMOKE FAILED: $($failed -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host ""
Write-Host "SMOKE PASSED" -ForegroundColor Green
exit 0
