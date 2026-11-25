"""GitHub Releases 更新檢查。"""

from __future__ import annotations

import json
import re
import shutil
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from . import __version__

API_URL = "https://api.github.com/repos/{repo}/releases/latest"
USER_AGENT = "LowPriceHunter-Updater/1.0"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class UpdateError(RuntimeError):
    pass


def parse_version(value: str) -> tuple[int, ...]:
    parts = [int(x) for x in re.findall(r"\d+", value or "")[:4]]
    return tuple(parts) if parts else (0,)


def is_newer(latest: str, current: str = __version__) -> bool:
    left = parse_version(latest)
    right = parse_version(current)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def choose_zip_asset(assets: list[dict]) -> dict | None:
    zips = [a for a in assets if str(a.get("name", "")).lower().endswith(".zip")]
    for token in ("update", "win", "portable"):
        for asset in zips:
            if token in str(asset.get("name", "")).lower():
                return asset
    return zips[0] if zips else None


@dataclass
class UpdateInfo:
    available: bool
    current: str
    latest: str
    url: str
    notes: str
    published_at: str
    asset_url: str | None
    asset_name: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def check_for_update(repo: str, timeout: int = 15) -> UpdateInfo:
    repo = (repo or "").strip().strip("/")
    if not _REPO_RE.match(repo):
        raise UpdateError("GitHub repo 格式需為 owner/repo")
    request = urllib.request.Request(
        API_URL.format(repo=repo),
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("這個 GitHub repo 還沒有 release") from exc
        if exc.code == 403:
            raise UpdateError("GitHub API 暫時被限制，請稍後再試") from exc
        raise UpdateError(f"GitHub 回應錯誤：{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"無法連線 GitHub：{exc.reason}") from exc

    tag = str(payload.get("tag_name") or "").strip()
    latest = tag.lstrip("vV")
    if not latest:
        raise UpdateError("release 沒有版本標籤")
    asset = choose_zip_asset(payload.get("assets") or [])
    return UpdateInfo(
        available=is_newer(latest),
        current=__version__,
        latest=latest,
        url=str(payload.get("html_url") or ""),
        notes=str(payload.get("body") or "")[:4000],
        published_at=str(payload.get("published_at") or ""),
        asset_url=str(asset.get("browser_download_url") or "") if asset else None,
        asset_name=str(asset.get("name") or "") if asset else None,
    )


def extract_update_package(zip_path: Path, extract_dir: Path) -> Path:
    """安全解壓更新 zip（防路徑穿越），驗證內含 LowPriceHunter.exe 並剝除任何 data/logs。"""
    extract_dir.mkdir(parents=True, exist_ok=True)
    root_resolved = extract_dir.resolve()
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                dest = (extract_dir / info.filename).resolve()
                if root_resolved != dest and root_resolved not in dest.parents:
                    raise UpdateError("更新檔內含不安全路徑")
            zf.extractall(extract_dir)
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"更新壓縮檔無法解壓：{exc}") from exc

    exe_hit = next(extract_dir.rglob("LowPriceHunter.exe"), None)
    if exe_hit is None:
        raise UpdateError("更新壓縮檔缺少 LowPriceHunter.exe")
    app_dir = exe_hit.parent
    # 防呆：更新檔絕不覆蓋使用者的 data 與 logs
    for protected in ("data", "logs"):
        p = app_dir / protected
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    return app_dir


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_apply_update_script(
    app_dir: Path,
    new_app_dir: Path,
    stage_dir: Path,
    pid: int,
    desktop_dir: Path | None = None,
) -> str:
    """產生脫離主程式執行的 PowerShell 更新腳本：等待退出 → 備份並覆蓋舊版 → 清除暫存 → 重啟。"""
    app_q = _ps_quote(str(app_dir))
    new_q = _ps_quote(str(new_app_dir))
    stage_q = _ps_quote(str(stage_dir))
    desk_q = _ps_quote(str(desktop_dir)) if desktop_dir else "''"
    return f"""$ErrorActionPreference = 'Stop'
$AppDir   = {app_q}
$NewDir   = {new_q}
$StageDir = {stage_q}
$DeskDir  = {desk_q}
$OldPid   = {int(pid)}
$Backup   = Join-Path $StageDir 'backup'

# 1. 等待原本的 LowPriceHunter 結束，逾時則強制關閉同目錄行程（含 WebView2 視窗）
for ($i = 0; $i -lt 20; $i++) {{
    if (-not (Get-Process -Id $OldPid -ErrorAction SilentlyContinue)) {{ break }}
    Start-Sleep -Milliseconds 500
}}
Get-Process LowPriceHunter, LowPriceHunterHost -ErrorAction SilentlyContinue | ForEach-Object {{
    try {{
        if ($_.Path -and $_.Path.StartsWith($AppDir, [System.StringComparison]::OrdinalIgnoreCase)) {{
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        }}
    }} catch {{}}
}}
Start-Sleep -Seconds 2

# 2. 備份舊版執行檔與 _internal，失敗時自動回滾（完全不動 data 與 logs）
New-Item -ItemType Directory -Path $Backup -Force | Out-Null
try {{
    $oldExe = Join-Path $AppDir 'LowPriceHunter.exe'
    $oldInt = Join-Path $AppDir '_internal'
    if (Test-Path $oldExe) {{ Move-Item $oldExe (Join-Path $Backup 'LowPriceHunter.exe') -Force }}
    if (Test-Path $oldInt) {{ Move-Item $oldInt (Join-Path $Backup '_internal') -Force }}

    Copy-Item (Join-Path $NewDir 'LowPriceHunter.exe') $oldExe -Force
    if (Test-Path (Join-Path $NewDir '_internal')) {{
        Copy-Item (Join-Path $NewDir '_internal') $oldInt -Recurse -Force
    }}
}} catch {{
    # 回滾舊版，確保程式不會壞掉
    if (Test-Path (Join-Path $Backup 'LowPriceHunter.exe')) {{
        Move-Item (Join-Path $Backup 'LowPriceHunter.exe') (Join-Path $AppDir 'LowPriceHunter.exe') -Force -ErrorAction SilentlyContinue
    }}
    if (Test-Path (Join-Path $Backup '_internal')) {{
        Remove-Item (Join-Path $AppDir '_internal') -Recurse -Force -ErrorAction SilentlyContinue
        Move-Item (Join-Path $Backup '_internal') (Join-Path $AppDir '_internal') -Force -ErrorAction SilentlyContinue
    }}
    exit 1
}}

# 3. 順手清理桌面可能殘留的舊 LowPriceHunter-update*.zip 與暫存目錄
if ($DeskDir -and (Test-Path $DeskDir)) {{
    Get-ChildItem -Path $DeskDir -Filter 'LowPriceHunter-update*.zip' -File -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}}

# 4. 自動啟動新版 LowPriceHunter
Start-Process -FilePath (Join-Path $AppDir 'LowPriceHunter.exe') -WorkingDirectory $AppDir

# 5. 清除更新暫存檔（含舊版備份與下載 zip）
Start-Sleep -Seconds 1
Remove-Item -Path $StageDir -Recurse -Force -ErrorAction SilentlyContinue
"""
