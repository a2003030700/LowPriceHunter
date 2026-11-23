"""GitHub Releases 更新檢查。"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass

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
