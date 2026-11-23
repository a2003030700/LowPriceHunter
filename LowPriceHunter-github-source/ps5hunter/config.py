"""設定檔與環境變數載入。"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def load_dotenv(path: Path) -> None:
    """極簡 .env 解析（不覆蓋已存在的環境變數）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


@dataclass
class Group:
    name: str
    url: str

    @property
    def ident(self) -> str:
        return group_ident(self.url)


def group_ident(url: str) -> str:
    """從社團網址取出 slug 或數字 ID。"""
    cleaned = url.split("?")[0].split("#")[0].rstrip("/")
    if "/groups/" in cleaned:
        return cleaned.split("/groups/", 1)[1].split("/")[0]
    return cleaned


@dataclass
class Config:
    groups: list[Group] = field(default_factory=list)
    threshold: int = 10_000
    interval_seconds: int = 300
    max_posts_per_group: int = 20
    notify_on_first_run: bool = False
    browser_channel: str = "msedge"
    headless: bool = True
    timezone: str = "Asia/Taipei"
    db_path: Path = DATA_DIR / "ps5hunter.db"
    profile_dir: Path = DATA_DIR / "browser_profile"
    log_path: Path = DATA_DIR / "ps5hunter.log"
    mode: str = "poll"
    notification_interval_seconds: int = 90
    full_scan_interval_seconds: int = 900

    # 郵件設定（從 .env 讀）
    gmail_user: str = ""
    gmail_app_password: str = ""
    mail_to: str = ""

    @property
    def mail_ready(self) -> bool:
        return bool(self.gmail_user and self.gmail_app_password)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def load_config(config_path: Path | None = None) -> Config:
    load_dotenv(ROOT / ".env")

    path = config_path or (ROOT / "config.json")
    raw: dict = {}
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8-sig"))

    groups = [
        Group(name=str(g.get("name") or group_ident(str(g["url"]))), url=str(g["url"]))
        for g in raw.get("groups", [])
        if g.get("url")
    ]

    cfg = Config(
        groups=groups,
        threshold=int(raw.get("threshold", 10_000)),
        interval_seconds=int(raw.get("interval_seconds", 300)),
        max_posts_per_group=int(raw.get("max_posts_per_group", 20)),
        notify_on_first_run=bool(raw.get("notify_on_first_run", False)),
        browser_channel=str(raw.get("browser_channel", "msedge")),
        headless=bool(raw.get("headless", True)),
        timezone=str(raw.get("timezone", "Asia/Taipei")),
        mode=str(raw.get("mode", "poll")),
        notification_interval_seconds=int(raw.get("notification_interval_seconds", 90)),
        full_scan_interval_seconds=int(raw.get("full_scan_interval_seconds", 900)),
        gmail_user=os.environ.get("GMAIL_USER", "").strip(),
        gmail_app_password=os.environ.get("GMAIL_APP_PASSWORD", "").strip(),
        mail_to=os.environ.get("MAIL_TO", "").strip() or os.environ.get("GMAIL_USER", "").strip(),
    )

    if os.environ.get("PS5_DB_PATH"):
        cfg.db_path = Path(os.environ["PS5_DB_PATH"])
    db = raw.get("db_path")
    if db:
        p = Path(db)
        cfg.db_path = p if p.is_absolute() else (ROOT / p)
    profile = raw.get("profile_dir")
    if profile:
        p = Path(profile)
        cfg.profile_dir = p if p.is_absolute() else (ROOT / p)
    if os.environ.get("PS5_PROFILE_DIR"):
        cfg.profile_dir = Path(os.environ["PS5_PROFILE_DIR"])
    log = raw.get("log_path")
    if log:
        p = Path(log)
        cfg.log_path = p if p.is_absolute() else (ROOT / p)
    if os.environ.get("PS5_HEADLESS"):
        cfg.headless = _env_bool("PS5_HEADLESS", cfg.headless)
    return cfg


def save_groups(groups: list[Group], config_path: Path | None = None) -> None:
    path = config_path or (ROOT / "config.json")
    raw: dict = {}
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    raw["groups"] = [{"name": g.name, "url": g.url} for g in groups]
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
