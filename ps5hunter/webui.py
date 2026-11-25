"""通用社團監控：本機 Web UI + 背景監控引擎。"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import random
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
import zipfile
from collections import deque
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

from .botstore import BotStore
from .channels import ChannelStore
from .config import ROOT, load_dotenv
from .db import Store
from . import __version__
from .updater import (
    UpdateError,
    build_apply_update_script,
    check_for_update,
    extract_update_package,
)
from .generic import analyze_detail, analyze_post, analyze_text, find_risk_words, rule_from_dict, Rule
from .notifier import (
    build_digest_message,
    build_discord_embed,
    build_generic_message,
    build_text_notification,
    send_discord,
    send_gmail,
    send_telegram,
)
try:  # PTT 價格調查是選用功能，缺少模組時不應影響主程式啟動
    from .ptt_survey import scan_ptt
except Exception:  # noqa: BLE001
    scan_ptt = None
from .survey import aggregate_daily

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def parse_recipients(value) -> list[str]:
    """把 email.to（字串或陣列）整理成去重後的有效地址。"""
    if isinstance(value, (list, tuple)):
        parts = [str(x) for x in value]
    else:
        parts = re.split(r"[,;\s]+", str(value or ""))
    out: list[str] = []
    for part in parts:
        email = part.strip()
        if email and _EMAIL_RE.match(email) and email not in out:
            out.append(email)
    return out[:20]


def _global_key(post) -> str:
    """跨社團去重用的鍵：post_id 優先，其次正規化網址，最後文字雜湊。"""
    if post.post_id:
        return "p:" + str(post.post_id)
    if post.url:
        clean = post.url.split("?")[0].split("#")[0].rstrip("/")
        m = re.search(r"/(?:posts|permalink)/(\d+)", clean)
        if m:
            return "p:" + m.group(1)
        return "u:" + clean
    return "t:" + hashlib.sha1(post.short_text.encode("utf-8")).hexdigest()
def _content_fingerprint(post) -> str | None:
    """跨社團同作者複製貼文去重指紋。"""
    author = re.sub(r"\s+", "", (getattr(post, "author", "") or "").lower())
    title = re.sub(r"\s+", "", (getattr(post, "listing_title", "") or "").lower())
    body = re.sub(r"\s+", "", (getattr(post, "text", "") or "").lower())[:200]
    core = f"{author}|{title}|{body}"
    if len(title) + len(body) < 16:
        return None
    return "fp:" + hashlib.sha1(core.encode("utf-8")).hexdigest()[:24]


def _next_wait_seconds(interval_seconds: int, elapsed_seconds: float, *, jitter: bool = True) -> float:
    """從本輪開始時間起算剩餘等待秒數（含防風控冷卻下限與 ±15% 隨機抖動）。"""
    base = max(20.0, float(interval_seconds) - max(0.0, float(elapsed_seconds)))
    if not jitter:
        return base
    return max(20.0, base * random.uniform(0.85, 1.15))


def _has_any_ready_channel(
    channels: list[str],
    bot: dict,
    recipients: list[str],
    channel_secrets: dict,
) -> bool:
    """只要任一已勾選渠道具備完整憑證即可發送（不再強制綁死 Email）。"""
    if "email" in channels and bot and recipients:
        return True
    if "telegram" in channels:
        tg = (channel_secrets or {}).get("telegram") or {}
        if tg.get("bot_token") and tg.get("chat_id"):
            return True
    if "discord" in channels:
        dc = (channel_secrets or {}).get("discord") or {}
        if dc.get("webhook_url"):
            return True
    return False


from .scraper import AuthError, CheckpointError, FBScraper, GroupPausedError, LayoutError

DATA = ROOT / "data"
DATA.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA / "app_config.json"
BOT_PATH = DATA / "bot_email.json"
BOT_STORE = BotStore(DATA)
CHANNEL_STORE = ChannelStore(DATA)
DB_PATH = DATA / "ui.db"
LOG_PATH = DATA / "ui.log"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
CSRF_TOKEN = secrets.token_urlsafe(32)

_SECRET_RES = (
    (re.compile(r"bot\d+:[A-Za-z0-9_-]{20,}"), "bot<token>"),
    (re.compile(r"https://discord(?:app)?\.com/api/webhooks/[^\s\"']+"), "https://discord.com/api/webhooks/<masked>"),
    (re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{30,}\b"), "<token>"),
)


def _mask_secrets(text: str) -> str:
    """日誌不可出現 Bot Token / Webhook 等憑證。"""
    for pattern, repl in _SECRET_RES:
        text = pattern.sub(repl, str(text))
    return text
if getattr(sys, "frozen", False):
    _BUNDLE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    UI_PATH = _BUNDLE / "ps5hunter" / "ui" / "index.html"
    ASSETS_DIR = _BUNDLE / "assets"
else:
    UI_PATH = Path(__file__).resolve().parent / "ui" / "index.html"
    ASSETS_DIR = ROOT / "assets"

STARTUP_DIR = Path(
    os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup")
)
STARTUP_FILE = STARTUP_DIR / "LowPriceHunter.vbs"
AUTOSTART_MARKER = DATA / "autostart.on"
UPDATE_STATE_PATH = DATA / "update_state.json"

DEFAULT_CONFIG = {
    "groups": [],
    "rules": [],
    "email": {"to": "", "mode": "bot"},
    "notify_channels": ["email"],
    "notify_mode": "instant",
    "digest_hour": 21,
    "autostart": False,
    "auto_monitor": False,
    "max_post_age_minutes": 10,
    "interval_seconds": 300,
    "max_posts_per_group": 80,
    "profile_dir": "data/ui_profile",
    "language": "zh-TW",
    "update_repo": "",
    "update_check_enabled": True,
    "update_check_hour": 9,
    "update_skipped": "",
}


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config() -> dict:
    cfg = _read_json(CONFIG_PATH, None)
    if not cfg:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        _write_json(CONFIG_PATH, cfg)
    for key, value in DEFAULT_CONFIG.items():
        cfg.setdefault(key, value)
    return cfg


def save_config(cfg: dict) -> None:
    _write_json(CONFIG_PATH, cfg)


def _startup_vbs() -> str:
    q = "Chr(34)"
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        run_expr = f'{q} & "{exe}" & {q} & " --no-browser"'
        workdir = exe.parent
    else:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        script = ROOT / "start_ui.py"
        run_expr = f'{q} & "{pythonw}" & {q} & " " & {q} & "{script}" & {q} & " --no-browser"'
        workdir = ROOT
    return (
        'Set sh = CreateObject("WScript.Shell")\n'
        f'sh.CurrentDirectory = "{workdir}"\n'
        f'sh.Run {run_expr}, 0, False\n'
    )


def sync_autostart(enabled: bool) -> bool:
    """切換開機自動啟動的啟用標記（由 Startup 裡的啟動腳本檢查）。"""
    try:
        if enabled:
            AUTOSTART_MARKER.write_text("1", encoding="utf-8")
        else:
            AUTOSTART_MARKER.unlink(missing_ok=True)
        return AUTOSTART_MARKER.exists()
    except Exception:
        return False


def ensure_bot() -> dict:
    logger = logging.getLogger("ps5hunter.webui")
    try:
        bot = BOT_STORE.load()
    except Exception as exc:  # noqa: BLE001
        logger.warning("讀取機器人憑證失敗：%s", exc)
        bot = {}
    if bot.get("user") and bot.get("app_password"):
        return bot
    load_dotenv(ROOT / ".env")
    user = os.environ.get("GMAIL_USER", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if user and password:
        bot = {"user": user, "app_password": password, "from_name": "社團監控機器人"}
        try:
            BOT_STORE.save(bot)
        except Exception as exc:  # noqa: BLE001
            logger.warning("無法加密儲存機器人憑證：%s", exc)
            return {}
        return bot
    return {}


class DequeHandler(logging.Handler):
    def __init__(self, sink: deque):
        super().__init__()
        self.sink = sink

    def emit(self, record):
        try:
            self.sink.append(self.format(record))
        except Exception:
            pass


class App:
    def __init__(self):
        self.config = load_config()
        self.login = {"status": "idle", "account": ""}
        self.discovered: list[dict] = []
        self.logs: deque = deque(maxlen=400)
        self.stop_event = threading.Event()
        self.monitor: threading.Thread | None = None
        self.browser_lock = threading.Lock()
        self.discovering = False
        self._digest_date: str | None = None
        self.survey: dict = {"status": "idle"}
        self.update: dict = {
            "status": "idle",
            "current": __version__,
            "available": False,
            "message": "尚未檢查",
        }
        self._setup_logging()
        if self.config.get("auto_monitor"):
            threading.Timer(2.0, self.start_monitor).start()
        threading.Thread(target=self._update_loop, daemon=True).start()

    # ---------------------------------------------------------------- logging
    def _setup_logging(self):
        self.logger = logging.getLogger("ps5hunter.webui")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()
        fmt = logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S")
        fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
        fh.setFormatter(fmt)
        self.logger.addHandler(fh)
        daily = TimedRotatingFileHandler(
            LOG_DIR / "ui.log", when="midnight", backupCount=14, encoding="utf-8"
        )
        daily.setFormatter(fmt)
        self.logger.addHandler(daily)
        dh = DequeHandler(self.logs)
        dh.setFormatter(fmt)
        self.logger.addHandler(dh)

    def log(self, message: str):
        self.logger.info(_mask_secrets(message))

    # ---------------------------------------------------------------- status
    @property
    def monitoring(self) -> bool:
        return bool(self.monitor and self.monitor.is_alive())

    def status(self) -> dict:
        bot = ensure_bot()
        return {
            "monitoring": self.monitoring,
            "discovering": self.discovering,
            "autostart": AUTOSTART_MARKER.exists() and STARTUP_FILE.exists(),
            "login": self.login,
            "discovered": self.discovered,
            "version": __version__,
            "update": self.update,
            "bot": {
                "configured": bool(bot),
                "user": bot.get("user", ""),
                "masked": _mask_email(bot.get("user", "")),
            },
            "logs": list(self.logs)[-120:],
        }

    # ---------------------------------------------------------------- actions
    def start_login(self) -> dict:
        if self.monitoring:
            return {"ok": False, "error": "請先停止監控，再重新登入。"}
        if self.login.get("status") == "waiting":
            return {"ok": False, "error": "登入視窗已經開啟，請在視窗中完成登入。"}
        if self.browser_lock.locked():
            return {"ok": False, "error": "瀏覽器忙碌中，請稍候。"}
        self.login = {"status": "waiting", "account": ""}
        threading.Thread(target=self._login_thread, daemon=True).start()
        return {"ok": True}

    def _login_thread(self):
        with self.browser_lock:
            try:
                profile = self._profile_dir()
                with FBScraper(profile, channel="chrome", headless=False) as fb:
                    fb.page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
                    deadline = time.time() + 600
                    while time.time() < deadline:
                        if fb.is_logged_in():
                            account = ""
                            try:
                                fb.page.goto("https://www.facebook.com/me", wait_until="domcontentloaded", timeout=30000)
                                time.sleep(2)
                                account = urlparse(fb.page.url).path.strip("/").split("/")[-1]
                            except Exception:
                                pass
                            self.login = {"status": "ok", "account": account}
                            self.log(f"Facebook 登入成功：{account or '已登入'}")
                            return
                        time.sleep(2)
                self.login = {"status": "timeout", "account": ""}
                self.log("登入等待逾時")
            except Exception as exc:  # noqa: BLE001
                self.login = {"status": "error", "account": "", "message": str(exc)}
                self.log(f"登入失敗：{exc}")

    def discover(self) -> dict:
        if self.monitoring:
            return {"ok": False, "error": "請先停止監控，再抓取社團。"}
        if self.browser_lock.locked() or self.discovering:
            return {"ok": False, "error": "瀏覽器忙碌中，請稍候。"}
        self.discovering = True
        threading.Thread(target=self._discover_thread, daemon=True).start()
        return {"ok": True}

    def _discover_thread(self):
        with self.browser_lock:
            try:
                with FBScraper(self._profile_dir(), channel="chrome", headless=True) as fb:
                    if not fb.is_logged_in():
                        raise AuthError("尚未登入 Facebook")
                    groups = fb.discover_groups()
                self.discovered = [{"name": n, "url": u} for n, u in groups]
                self.log(f"抓到 {len(self.discovered)} 個已加入社團")
            except Exception as exc:  # noqa: BLE001
                self.log(f"抓取社團失敗：{exc}")
            finally:
                self.discovering = False

    def bot_status(self) -> dict:
        bot = ensure_bot()
        return {
            "ok": True,
            "configured": bool(bot),
            "user": bot.get("user", ""),
            "masked": _mask_email(bot.get("user", "")),
            "from_name": bot.get("from_name", "Low Price Hunter Bot"),
        }

    def save_bot(self, data: dict) -> dict:
        user = str(data.get("user") or "").strip()
        password = str(data.get("app_password") or "").strip()
        if not _EMAIL_RE.match(user):
            return {"ok": False, "error": "請填寫有效的機器人 Gmail。"}
        existing = ensure_bot()
        if not password:
            password = str(existing.get("app_password") or "").strip()
        if not password:
            return {"ok": False, "error": "請填寫應用程式密碼。"}
        bot = {
            "user": user,
            "app_password": password,
            "from_name": str(
                data.get("from_name") or existing.get("from_name") or "Low Price Hunter Bot"
            ),
        }
        try:
            BOT_STORE.save(bot)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"無法加密儲存機器人憑證：{exc}"}
        self.log(f"機器人信箱已更新：{_mask_email(user)}")
        return {"ok": True, "masked": _mask_email(user)}

    def clear_bot(self) -> dict:
        try:
            BOT_STORE.clear()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        self.log("機器人信箱憑證已清除")
        return {"ok": True}

    def channels_status(self) -> dict:
        try:
            data = CHANNEL_STORE.load()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "telegram": {}, "discord": {}}
        tg = data.get("telegram") or {}
        dc = data.get("discord") or {}
        return {
            "ok": True,
            "telegram": {
                "configured": bool(tg.get("bot_token") and tg.get("chat_id")),
                "chat_id": tg.get("chat_id", ""),
            },
            "discord": {"configured": bool(dc.get("webhook_url"))},
        }

    def save_channels(self, data: dict) -> dict:
        try:
            existing = CHANNEL_STORE.load()
        except Exception:  # noqa: BLE001
            existing = {}
        tg = existing.get("telegram") or {}
        dc = existing.get("discord") or {}
        token = str(data.get("telegram_token") or "").strip() or tg.get("bot_token", "")
        chat = str(data.get("telegram_chat_id") or "").strip() or tg.get("chat_id", "")
        webhook = str(data.get("discord_webhook") or "").strip() or dc.get("webhook_url", "")
        try:
            CHANNEL_STORE.save(
                {
                    "telegram": {"bot_token": token, "chat_id": chat},
                    "discord": {"webhook_url": webhook},
                }
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"無法加密儲存渠道憑證：{exc}"}
        self.log("通知渠道憑證已更新")
        return {"ok": True, "telegram": bool(token and chat), "discord": bool(webhook)}

    def test_channel(self, channel: str) -> dict:
        try:
            data = CHANNEL_STORE.load()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        if channel == "email":
            return self.test_email()
        try:
            if channel == "telegram":
                tg = data.get("telegram") or {}
                send_telegram(tg.get("bot_token", ""), tg.get("chat_id", ""), "Low Price Hunter 測試通知")
                return {"ok": True}
            if channel == "discord":
                dc = data.get("discord") or {}
                embed = build_discord_embed(
                    "測試通知", 9500, "Low Price Hunter 測試社團",
                    "https://github.com/a2003030700/LowPriceHunter",
                    "如果你在 Discord 收到這張卡片，代表即時推播設定成功！",
                )
                send_discord(dc.get("webhook_url", ""), "🔔 **Low Price Hunter** Discord 即時推播測試成功！", embed=embed)
                return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": "未知渠道"}

    def price_history(self) -> dict:
        store = Store(DB_PATH)
        try:
            summary = [
                {
                    "rule": row[0] or "其他",
                    "count": row[1],
                    "min": row[2],
                    "avg": round(row[3] or 0),
                    "max": row[4],
                }
                for row in store.price_summary()
            ]
            recent = [
                {
                    "rule": row[0] or "其他",
                    "group": row[1] or "",
                    "price": row[2],
                    "price_high": row[3],
                    "at": row[4],
                }
                for row in store.recent_prices(50)
            ]
            return {"ok": True, "summary": summary, "recent": recent}
        finally:
            store.close()

    def fetch_post(self, url: str) -> dict:
        parsed = urlparse(url or "")
        if not parsed.scheme.startswith("http") or "facebook.com" not in parsed.netloc:
            return {"ok": False, "error": "請貼 Facebook 貼文或社團網址"}
        if self.monitoring:
            return {"ok": False, "error": "監控中，請先停止監控再抓取"}
        if not self.browser_lock.acquire(timeout=10):
            return {"ok": False, "error": "瀏覽器忙碌中，請稍後"}
        try:
            with FBScraper(self._profile_dir(), channel="chrome", headless=True) as fb:
                if not fb.is_logged_in():
                    return {"ok": False, "error": "尚未登入 Facebook"}
                clean = url.split("?")[0]
                if "/groups/" in clean and "/posts/" not in clean and "/permalink/" not in clean:
                    posts = fb.scrape_group("測試社團", url, max_posts=1, resolve_when=None)
                    if not posts:
                        return {"ok": False, "error": "抓不到貼文"}
                    text = posts[0].text
                else:
                    text = fb.read_post_text(url)
                if not text.strip():
                    return {
                        "ok": False,
                        "error": "Facebook 沒有回傳貼文內容（可能被限制、貼文已刪除，或登入狀態需要重新整理）",
                    }
                return {"ok": True, "text": text[:6000]}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            self.browser_lock.release()

    def start_price_survey(self, rule_name: str) -> dict:
        if self.survey.get("status") == "running":
            return {"ok": False, "error": "價格調查已在進行"}
        rule_raw = next(
            (r for r in self.config.get("rules", []) if str(r.get("name")) == rule_name),
            None,
        )
        if not rule_raw:
            return {"ok": False, "error": "找不到這個物品"}
        rule = rule_from_dict(rule_raw)
        self.survey = {
            "status": "running",
            "rule": rule_name,
            "done": 0,
            "total": 0,
            "message": "連線 PTT Gamesale",
            "points": [],
            "daily": [],
            "summary": {},
        }
        threading.Thread(target=self._run_survey, args=(rule,), daemon=True).start()
        return {"ok": True}

    def _run_survey(self, rule: Rule) -> None:
        try:
            if scan_ptt is None:
                raise RuntimeError("這個版本未包含 PTT 價格調查功能")

            def progress(done: int, total: int, message: str) -> None:
                self.survey.update(done=done, total=total, message=message)

            points = scan_ptt(rule, max_posts=40, days=7, progress=progress)
            prices = [p["price"] for p in points]
            self.survey.update(
                status="done",
                points=points,
                daily=aggregate_daily(points),
                summary=(
                    {
                        "min": min(prices),
                        "max": max(prices),
                        "avg": round(sum(prices) / len(prices)),
                    }
                    if prices
                    else {}
                ),
            )
            self.log(f"PTT 價格調查完成：{rule.name} {len(points)} 筆")
        except Exception as exc:  # noqa: BLE001
            self.survey.update(status="error", error=str(exc))
            self.log(f"PTT 價格調查失敗：{exc}")

    def survey_status(self) -> dict:
        data = dict(self.survey)
        if data.get("status") == "running":
            data.pop("points", None)
            data.pop("daily", None)
        return data

    def export_logs(self) -> dict:
        try:
            desktop = _desktop_dir()
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            name = f"LowPriceHunter-logs-{ts}.zip"
            recent = [
                line
                for line in list(self.logs)[-80:]
                if any(key in line for key in ("錯誤", "失敗", "Exception", "Traceback"))
            ]
            bot = ensure_bot()
            summary = "\n".join(
                [
                    "Low Price Hunter 診斷包",
                    f"時間：{datetime.now():%Y-%m-%d %H:%M:%S}",
                    f"監控：{'執行中' if self.monitoring else '停止'}",
                    f"登入：{self.login.get('status', '')}",
                    f"機器人：{_mask_email(bot.get('user', ''))}",
                    f"社團：{len(self.config.get('groups') or [])}",
                    f"規則：{len(self.config.get('rules') or [])}",
                    "",
                    "最近錯誤：",
                    *(recent or ["（無）"]),
                ]
            )
            last_exc: Exception | None = None
            for out in (desktop / name, DATA / "exports" / name):
                try:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    build_log_bundle(LOG_DIR, out, summary, self.config)
                    self.log(f"已輸出日誌：{out}")
                    return {"ok": True, "path": str(out)}
                except OSError as exc:
                    last_exc = exc
            return {"ok": False, "error": str(last_exc)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def open_logs(self) -> dict:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(LOG_DIR))
            else:
                webbrowser.open(LOG_DIR.as_uri())
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def test_email(self) -> dict:
        recipients = parse_recipients((self.config.get("email") or {}).get("to", ""))
        if not recipients:
            return {"ok": False, "error": "請先填寫至少一個有效的收件 Email。"}
        bot = ensure_bot()
        if not bot:
            return {"ok": False, "error": "機器人信箱未設定（data/bot_email.dpapi）。"}
        sent = 0
        errors: list[str] = []
        for to in recipients:
            try:
                from email.message import EmailMessage
                from email.utils import formatdate

                msg = EmailMessage()
                msg["Subject"] = "✅ 社團監控測試信"
                msg["From"] = bot["user"]
                msg["To"] = to
                msg["Date"] = formatdate(localtime=True)
                msg.set_content("如果你收到這封信，代表通知設定成功。")
                msg.add_alternative("<p style='font-family:sans-serif'>通知設定成功 ✅</p>", subtype="html")
                send_gmail(msg, bot["user"], bot["app_password"])
                sent += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{to}: {exc}")
        if sent:
            self.log(f"測試信已寄到 {', '.join(recipients)}")
            return {"ok": True, "count": sent}
        self.log(f"測試信失敗：{'; '.join(errors)}")
        return {"ok": False, "error": "; ".join(errors)}

    def start_monitor(self) -> dict:
        if self.monitoring:
            return {"ok": False, "error": "監控已在執行。"}
        if self.login.get("status") == "waiting" or self.browser_lock.locked():
            return {"ok": False, "error": "瀏覽器忙碌中，請稍候。"}
        if not self.config.get("groups"):
            return {"ok": False, "error": "請至少加入一個社團。"}
        if not any(r.get("keywords") for r in self.config.get("rules", [])):
            return {"ok": False, "error": "請至少設定一條有關鍵字的監控規則。"}
        self.stop_event.clear()
        self.monitor = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor.start()
        return {"ok": True}

    def stop_monitor(self) -> dict:
        self.stop_event.set()
        self.log("已要求停止監控")
        return {"ok": True}

    # ---------------------------------------------------------------- updates
    def _load_update_state(self) -> dict:
        return _read_json(UPDATE_STATE_PATH, {})

    def _save_update_state(self, data: dict) -> None:
        _write_json(UPDATE_STATE_PATH, data)

    def _update_loop(self) -> None:
        while True:
            time.sleep(30)
            try:
                if not self.config.get("update_check_enabled", True):
                    continue
                repo = str(self.config.get("update_repo") or "").strip()
                if not repo:
                    continue
                hour = int(self.config.get("update_check_hour") or 9)
                if datetime.now().hour < hour:
                    continue
                today = datetime.now().strftime("%Y-%m-%d")
                if self._load_update_state().get("last_check") == today:
                    continue
                if self.update.get("status") == "checking":
                    continue
                self.check_update()
            except Exception:  # noqa: BLE001
                pass

    def check_update(self) -> dict:
        if self.update.get("status") == "checking":
            return {"ok": False, "error": "正在檢查更新"}
        repo = str(self.config.get("update_repo") or "").strip()
        if not repo:
            self.update = {
                "status": "idle",
                "current": __version__,
                "available": False,
                "message": "請先在設定填入 GitHub repo（owner/repo）",
            }
            return {"ok": False, "error": "尚未設定 GitHub repo"}
        self.update = {
            "status": "checking",
            "current": __version__,
            "available": False,
            "message": "正在檢查 GitHub Releases…",
        }
        threading.Thread(target=self._check_update_thread, args=(repo,), daemon=True).start()
        return {"ok": True}

    def _check_update_thread(self, repo: str) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            info = check_for_update(repo).to_dict()
            skipped = info.get("latest") == str(self.config.get("update_skipped") or "")
            info["skipped"] = skipped
            if skipped:
                info["available"] = False
            info["status"] = "done"
            info["checked_at"] = datetime.now().isoformat(timespec="seconds")
            info["checked_date"] = today
            info["message"] = (
                f"有新版本 {info['latest']}"
                if info["available"]
                else ("已略過這個版本" if skipped else f"已是最新版本 {info['current']}")
            )
            self.update = info
            self._save_update_state({"last_check": today, "latest": info.get("latest")})
            if info["available"]:
                self.log(f"發現新版本：{info['latest']}（目前 {info['current']}）")
            else:
                self.log(f"更新檢查：{info['message']}")
        except Exception as exc:  # noqa: BLE001
            self.update = {
                "status": "error",
                "current": __version__,
                "available": False,
                "message": str(exc),
                "error": str(exc),
                "checked_at": datetime.now().isoformat(timespec="seconds"),
                "checked_date": today,
            }
            self._save_update_state({"last_check": today})
            self.log(f"更新檢查失敗：{exc}")

    def skip_update(self, version: str) -> dict:
        version = (version or "").strip()
        if not version:
            return {"ok": False, "error": "缺少版本號"}
        self.config["update_skipped"] = version
        save_config(self.config)
        self.update["available"] = False
        self.update["skipped"] = True
        self.update["message"] = f"已略過版本 {version}"
        return {"ok": True}

    def open_update_page(self) -> dict:
        url = str(self.update.get("url") or "")
        if not url:
            return {"ok": False, "error": "沒有 release 頁面"}
        webbrowser.open(url)
        return {"ok": True}

    def download_update(self) -> dict:
        url = str(self.update.get("asset_url") or "")
        if not url:
            return {"ok": False, "error": "這個 release 沒有 zip 更新檔"}
        name = Path(str(self.update.get("asset_name") or "")).name
        if not name.lower().endswith(".zip"):
            name = f"LowPriceHunter-{self.update.get('latest') or 'update'}.zip"
        name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
        target = _desktop_dir() / name
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "LowPriceHunter-Updater/1.0"})
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read(200 * 1024 * 1024 + 1)
            if len(data) > 200 * 1024 * 1024:
                return {"ok": False, "error": "更新檔超過 200MB"}
            target.write_bytes(data)
            self.log(f"已下載更新檔：{target}")
            return {"ok": True, "path": str(target)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def apply_update(self) -> dict:
        """一鍵自動套用更新：下載驗證 → 停止監控 → 脫離背景覆蓋舊版並清除暫存 → 自動重啟。"""
        if not getattr(sys, "frozen", False):
            return {"ok": False, "error": "原始碼執行模式請直接使用 git pull 更新，無需覆蓋執行檔。"}
        if sys.platform != "win32":
            return {"ok": False, "error": "一鍵自動覆蓋僅支援 Windows 執行檔版本。"}
        url = str(self.update.get("asset_url") or "")
        if not url:
            return {"ok": False, "error": "這個 release 沒有可套用的 zip 更新檔"}

        stage_dir = Path(tempfile.mkdtemp(prefix="lph_upd_"))
        zip_path = stage_dir / "update.zip"
        try:
            self.log(f"開始下載並驗證更新檔（v{self.update.get('latest') or '?'}）…")
            request = urllib.request.Request(url, headers={"User-Agent": "LowPriceHunter-Updater/1.0"})
            with urllib.request.urlopen(request, timeout=180) as response:
                data = response.read(200 * 1024 * 1024 + 1)
            if len(data) > 200 * 1024 * 1024:
                raise UpdateError("更新檔超過 200MB")
            zip_path.write_bytes(data)
            new_app_dir = extract_update_package(zip_path, stage_dir / "extracted")
            # 解壓驗證通過即可先刪 zip，節省空間
            zip_path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(stage_dir, ignore_errors=True)
            self.log(f"自動套用更新失敗：{exc}")
            return {"ok": False, "error": str(exc)}

        # 先溫和通知監控停止，讓 SQLite 寫入完成
        if self.monitoring:
            self.stop_event.set()
            if self.monitor:
                self.monitor.join(timeout=5)

        app_dir = Path(sys.executable).resolve().parent
        script_path = stage_dir / "apply_update.ps1"
        script_text = build_apply_update_script(
            app_dir=app_dir,
            new_app_dir=new_app_dir,
            stage_dir=stage_dir,
            pid=os.getpid(),
            desktop_dir=_desktop_dir(),
        )
        script_path.write_text(script_text, encoding="utf-8-sig")

        flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(script_path),
            ],
            cwd=str(stage_dir),
            close_fds=True,
            creationflags=flags,
        )
        self.log(f"正在自動覆蓋更新至 v{self.update.get('latest') or '?'} 並重新啟動…")
        threading.Timer(0.8, lambda: os._exit(0)).start()
        return {"ok": True, "restarting": True, "version": str(self.update.get("latest") or "")}

    def logout(self) -> dict:
        """停止監控並清除本機 Facebook 登入 session。"""
        if self.login.get("status") == "waiting":
            return {"ok": False, "error": "登入視窗還開著，請先關閉它再登出。"}

        if self.monitoring:
            self.stop_event.set()
            if self.monitor:
                self.monitor.join(timeout=40)

        if not self.browser_lock.acquire(timeout=60):
            return {"ok": False, "error": "掃描仍在進行，請稍後再試。"}
        try:
            profile = self._profile_dir()
            if profile.exists():
                shutil.rmtree(profile, ignore_errors=True)
            self.login = {"status": "idle", "account": ""}
            self.log("已登出 Facebook，登入 session 已清除")
            return {"ok": True, "removed": not profile.exists()}
        finally:
            self.browser_lock.release()

    def check_login(self) -> dict:
        """用抓取端（Playwright 設定檔）實際檢查 Facebook 登入是否有效。"""
        if self.monitoring:
            return {"ok": True, "login": self.login}
        if self.login.get("status") == "waiting":
            return {"ok": False, "error": "登入視窗仍開著", "login": self.login}
        if not self._profile_dir().exists():
            self.login = {"status": "idle", "account": ""}
            return {"ok": True, "login": self.login}
        if not self.browser_lock.acquire(timeout=15):
            return {"ok": False, "error": "瀏覽器忙碌中", "login": self.login}
        try:
            with FBScraper(self._profile_dir(), channel="chrome", headless=True) as fb:
                ok = fb.is_logged_in()
            if ok:
                self.login = {"status": "ok", "account": self.login.get("account", "")}
            else:
                self.login = {"status": "error", "account": ""}
            self.log("登入狀態檢查：" + ("有效" if ok else "已失效"))
            return {"ok": True, "login": self.login}
        except Exception as exc:  # noqa: BLE001
            self.login = {"status": "error", "account": ""}
            self.log(f"登入狀態檢查失敗：{exc}")
            return {"ok": False, "error": str(exc), "login": self.login}
        finally:
            self.browser_lock.release()

    # ---------------------------------------------------------------- monitor
    def _profile_dir(self) -> Path:
        p = Path(self.config.get("profile_dir") or "data/ui_profile")
        return p if p.is_absolute() else (ROOT / p)

    def _monitor_loop(self):
        store = Store(DB_PATH)
        first_run = store.is_fresh()
        self.log("監控執行緒啟動（常駐瀏覽器快速模式）")
        fb: FBScraper | None = None
        fb_started_at = 0.0
        fb_profile: Path | None = None
        try:
            while not self.stop_event.is_set():
                cycle_t0 = time.monotonic()
                try:
                    profile = self._profile_dir()
                    # 每 60 分鐘或更換設定檔時定期重啟 Chrome，避免長時掛機吃滿記憶體
                    if fb is not None and (
                        fb_profile != profile or (time.monotonic() - fb_started_at) >= 3600
                    ):
                        try:
                            fb.__exit__(None, None, None)
                        except Exception:
                            pass
                        fb = None
                    if fb is None:
                        with self.browser_lock:
                            fb = FBScraper(profile, channel="chrome", headless=True)
                            fb.__enter__()
                            fb_started_at = time.monotonic()
                            fb_profile = profile
                    self._cycle(store, first_run, fb=fb)
                    first_run = False
                    store.set_baseline()
                except (AuthError, CheckpointError) as exc:
                    self.log(f"需要人工處理：{exc}")
                    break
                except Exception as exc:  # noqa: BLE001
                    self.log(f"掃描失敗：{exc}")
                    # 發生瀏覽器異常時釋放舊實例，下輪自動重開
                    if fb is not None:
                        try:
                            fb.__exit__(None, None, None)
                        except Exception:
                            pass
                        fb = None
                elapsed = time.monotonic() - cycle_t0
                interval = int(self.config.get("interval_seconds") or 120)
                wait_sec = _next_wait_seconds(interval, elapsed, jitter=True)
                if self.stop_event.wait(wait_sec):
                    break
        finally:
            if fb is not None:
                try:
                    fb.__exit__(None, None, None)
                except Exception:
                    pass
            store.close()
            self.log("監控已停止")

    def _dispatch_immediate(
        self,
        store: Store,
        item: dict,
        channels: list[str],
        bot: dict,
        recipients: list[str],
        channel_secrets: dict,
    ) -> bool:
        """單一貼文命中後立刻去重並推播（不必等待其餘社團掃完）。"""
        gkey = item["global"]
        dedup_key = item.get("fp") or gkey
        group_name = item["group"]
        sources = [group_name]

        # 若該貼文（依內容指紋或 post_id）已透過其他社團通知過，直接記錄並跳過
        if store.seen_global(gkey) or not store.claim_global(dedup_key, sources):
            store.remember(
                item["key"], group_name, item["url"],
                item["match"].price, item["post"].text, True,
                rule_name=item["rule"].name,
            )
            return True
        if gkey != dedup_key:
            store.remember_global(gkey, sources)

        risks = sorted(set(item.get("risk") or []))
        price = item["match"].price or 0
        price_high = item["match"].price_high
        is_split = bool(getattr(item["match"], "is_split_sale", False))
        text_message = build_text_notification(
            item["rule"].name, price,
            group_name, item["url"], item["post"].short_text,
            price_high=price_high,
            sources=sources,
            risk_words=risks,
        )
        sent_channels: list[str] = []

        # 速度優先：先發 Discord / Telegram（通常 < 0.5 秒），再寄 Email SMTP
        if "discord" in channels:
            dc = (channel_secrets or {}).get("discord") or {}
            if dc.get("webhook_url"):
                try:
                    price_str = f"NT$ {price:,.0f}" + (f"–{price_high:,.0f}" if price_high and price_high != price else "")
                    split_tag = "（拆售價）" if is_split else ""
                    warn_tag = "⚠️ " if risks else "🔔 "
                    summary_line = f"{warn_tag}**{item['rule'].name}** {price_str}{split_tag}｜{group_name}"
                    embed = build_discord_embed(
                        item["rule"].name, price, group_name, item["url"],
                        item["post"].short_text,
                        price_high=price_high,
                        sources=sources,
                        risk_words=risks,
                        is_split_sale=is_split,
                    )
                    send_discord(dc["webhook_url"], summary_line, embed=embed)
                    sent_channels.append("Discord")
                except Exception as exc:  # noqa: BLE001
                    self.log(f"Discord 失敗：{exc}")
            else:
                self.log("Discord 未設定，略過")

        if "telegram" in channels:
            tg = (channel_secrets or {}).get("telegram") or {}
            if tg.get("bot_token") and tg.get("chat_id"):
                try:
                    send_telegram(tg["bot_token"], tg["chat_id"], text_message)
                    sent_channels.append("Telegram")
                except Exception as exc:  # noqa: BLE001
                    self.log(f"Telegram 失敗：{exc}")
            else:
                self.log("Telegram 未設定，略過")

        if "email" in channels and bot and recipients:
            mail_count = 0
            for recipient in recipients:
                try:
                    msg = build_generic_message(
                        item["rule"].name, price,
                        group_name, item["url"], item["post"].short_text,
                        bot["user"], recipient,
                        price_high=price_high,
                        sources=sources,
                        risk_words=risks,
                    )
                    send_gmail(msg, bot["user"], bot["app_password"])
                    mail_count += 1
                except Exception as exc:  # noqa: BLE001
                    self.log(f"寄給 {recipient} 失敗：{exc}")
            if mail_count:
                sent_channels.append(f"Email {mail_count} 人")

        if sent_channels:
            store.remember(
                item["key"], group_name, item["url"],
                item["match"].price, item["post"].text, True,
                rule_name=item["rule"].name,
            )
            store.record_price(
                dedup_key, item["rule"].name, group_name,
                price, price_high,
            )
            price_label = f"NT${price:,}"
            if price_high and price_high != price:
                price_label += f"–{price_high:,}"
            risk_text = f"，高風險：{'、'.join(risks)}" if risks else ""
            self.log(
                f"已即時通知：{item['rule'].name} {price_label}"
                f"（{', '.join(sent_channels)}，{group_name}{risk_text}）｜{item['url']}"
            )
            return True

        store.release_global(dedup_key)
        if gkey != dedup_key:
            store.release_global(gkey)
        self.log("通知失敗：沒有任何渠道成功，游標暫不推進")
        return False

    def _cycle(self, store: Store, first_run: bool, fb: FBScraper | None = None):
        cycle_t0 = time.monotonic()
        rules = [rule_from_dict(r) for r in self.config.get("rules", [])]
        groups = self.config.get("groups", [])
        recipients = parse_recipients((self.config.get("email") or {}).get("to", ""))
        bot = ensure_bot()
        channels = self.config.get("notify_channels") or ["email"]
        try:
            channel_secrets = CHANNEL_STORE.load()
        except Exception:  # noqa: BLE001
            channel_secrets = {}
        self.log(f"開始掃描 {len(groups)} 個社團、{len(rules)} 條規則")

        own_fb = fb is None
        with self.browser_lock:
            if own_fb:
                fb = FBScraper(self._profile_dir(), channel="chrome", headless=True)
                fb.__enter__()
            try:
                assert fb is not None
                if not fb.is_logged_in():
                    self.login = {"status": "error", "account": ""}
                    raise AuthError("尚未登入 Facebook")
                self.login = {"status": "ok", "account": self.login.get("account", "")}

                ready_channel = _has_any_ready_channel(channels, bot, recipients, channel_secrets)

                for group in groups:
                    if self.stop_event.is_set():
                        break
                    if "rules" in group and isinstance(group["rules"], list):
                        names = {str(x) for x in group["rules"]}
                        group_rules = [r for r in rules if r.name in names]
                    else:
                        group_rules = rules
                    if not group_rules:
                        self.log(f"跳過 {group.get('name')}：沒有套用任何規則")
                        continue
                    gid = group["url"].split("/groups/", 1)[1].split("/")[0].split("?")[0]
                    cursor = store.get_cursor(gid)
                    stop_ts = None
                    if cursor and cursor.get("last_seen_ts"):
                        try:
                            stop_ts = datetime.fromisoformat(cursor["last_seen_ts"])
                        except ValueError:
                            stop_ts = None
                    expand_kws: list[str] = []
                    for r in group_rules:
                        expand_kws.extend(r.keywords)
                        expand_kws.extend(r.require)
                    try:
                        posts = fb.scrape_group(
                            group.get("name") or "社團",
                            group["url"],
                            max_posts=int(self.config.get("max_posts_per_group") or 30),
                            stop_at_post_id=(cursor or {}).get("last_post_id"),
                            stop_at_ts=stop_ts,
                            expand_keywords=expand_kws,
                            resolve_when_post=lambda p, gr=group_rules: any(analyze_post(p, r).matched for r in gr),
                            is_seen=(store.seen if cursor is not None else None),
                            should_stop=self.stop_event.is_set,
                        )
                    except GroupPausedError:
                        self.log(f"跳過 {group.get('name')}：社團已被管理員暫停")
                        continue
                    except (LayoutError, CheckpointError) as exc:
                        self.log(f"跳過 {group.get('name')}：{exc}")
                        continue
                    if not posts:
                        continue
                    newest = _newest_post(posts)
                    newest_ts = (
                        newest.created_at.isoformat(timespec="seconds")
                        if newest.created_at is not None
                        else None
                    )
                    newest_id = newest.post_id or newest.key

                    if first_run and cursor is None:
                        for post in posts:
                            store.remember(
                                post.key, group.get("name", ""), post.url or group["url"],
                                None, post.text, True,
                            )
                        store.set_cursor(gid, newest_id, newest_ts)
                        self.log(f"首次掃描 {group.get('name')}：建立游標（{len(posts)} 篇，不補發）")
                        continue

                    group_ok = True
                    max_age = float(self.config.get("max_post_age_minutes") or 0)
                    for post in posts:
                        key = post.key
                        if store.seen(key):
                            continue
                        best_rule = None
                        best_match = None
                        for rule in group_rules:
                            result = analyze_post(post, rule)
                            if not result.matched:
                                continue
                            if best_match is None or (result.price or 10**9) < (best_match.price or 10**9):
                                best_rule, best_match = rule, result
                        url = post.url or group["url"]
                        if best_match is None:
                            store.remember(key, group.get("name", ""), url, None, post.text, True)
                            continue
                        if max_age > 0:
                            too_old = post.age_minutes is not None and post.age_minutes > max_age
                            unknown_without_cursor = post.age_minutes is None and cursor is None
                            if too_old or unknown_without_cursor:
                                store.remember(key, group.get("name", ""), url, best_match.price, post.text, True)
                                self.log(
                                    f"略過非 {max_age:g} 分鐘內貼文：{best_rule.name}"
                                    f"（{post.age_minutes if post.age_minutes is not None else '時間不明'}）"
                                )
                                continue
                        if self.config.get("notify_mode") == "digest":
                            store.remember(
                                key, group.get("name", ""), url, best_match.price, post.text, False,
                                rule_name=best_rule.name,
                            )
                            store.record_price(
                                key, best_rule.name, group.get("name", ""),
                                best_match.price or 0, best_match.price_high,
                            )
                            self.log(f"加入每日摘要：{best_rule.name} NT${best_match.price:,}")
                            continue
                        if not ready_channel:
                            self.log("符合條件但沒有任何已設定的通知渠道（Email／Discord／Telegram），游標暫不推進")
                            group_ok = False
                            continue
                        item = {
                            "post": post,
                            "key": key,
                            "url": url,
                            "group": group.get("name", ""),
                            "group_id": gid,
                            "rule": best_rule,
                            "match": best_match,
                            "global": _global_key(post),
                            "fp": _content_fingerprint(post),
                            "risk": find_risk_words(post.short_text),
                        }
                        if not self._dispatch_immediate(
                            store, item, channels, bot, recipients, channel_secrets
                        ):
                            group_ok = False

                    if group_ok:
                        store.set_cursor(gid, newest_id, newest_ts)
                    else:
                        self.log(f"{group.get('name', '')}：有貼文未成功通知，游標不推進，下輪重試")
            finally:
                if own_fb and fb is not None:
                    try:
                        fb.__exit__(None, None, None)
                    except Exception:
                        pass
        elapsed = time.monotonic() - cycle_t0
        self.log(f"本輪掃描完成（耗時 {elapsed:.1f} 秒）")
        self._maybe_send_digest(store, bot, recipients)

    def _maybe_send_digest(self, store: Store, bot: dict, recipients: list[str]):
        if self.config.get("notify_mode") != "digest" or not (bot and recipients):
            return
        now = datetime.now()
        hour = int(self.config.get("digest_hour") or 21)
        today = now.strftime("%Y-%m-%d")
        if now.hour < hour or self._digest_date == today:
            return
        rows = store.pending()
        if not rows:
            return
        items = [
            {
                "key": r[0],
                "group": r[1] or "",
                "url": r[2] or "",
                "price": r[3] or 0,
                "text": r[4] or "",
                "rule": r[5] or "其他",
            }
            for r in rows
        ]
        sent = 0
        for recipient in recipients:
            try:
                msg = build_digest_message(items, bot["user"], recipient, today)
                send_gmail(msg, bot["user"], bot["app_password"])
                sent += 1
            except Exception as exc:  # noqa: BLE001
                self.log(f"每日摘要寄給 {recipient} 失敗：{exc}")
        if sent:
            store.mark_notified([i["key"] for i in items])
            self._digest_date = today
            self.log(f"已寄出每日摘要：{len(items)} 筆（{sent} 人）")
        else:
            self.log("每日摘要寄送失敗：沒有任何收件者成功")


_MASK_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")


def _sanitize_text(text: str) -> str:
    return _MASK_EMAIL_RE.sub("***@***", text or "")


def _sanitize_config(cfg: dict) -> dict:
    data = json.loads(json.dumps(cfg))
    email_cfg = data.get("email") or {}
    to = email_cfg.get("to")
    if isinstance(to, list):
        email_cfg["to"] = ["***" for _ in to]
    elif to:
        email_cfg["to"] = "***"
    data["email"] = email_cfg
    return data


def _desktop_dir() -> Path:
    for candidate in (
        Path(os.environ.get("USERPROFILE", "")) / "Desktop",
        Path.home() / "Desktop",
    ):
        if candidate.is_dir():
            return candidate
    return Path.home()


def build_log_bundle(log_dir: Path, out_path: Path, summary: str, config: dict) -> None:
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("summary.txt", _sanitize_text(summary))
        zf.writestr(
            "config.json",
            json.dumps(_sanitize_config(config), ensure_ascii=False, indent=2),
        )
        for path in sorted(log_dir.glob("ui.log*")):
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                zf.writestr(f"logs/{path.name}", _sanitize_text(text))


def _newest_post(posts: list) -> object:
    dated = [p for p in posts if p.created_at is not None]
    if dated:
        return max(dated, key=lambda p: p.created_at)
    return posts[0]


def _mask_email(email: str) -> str:
    if "@" not in email:
        return email
    name, domain = email.split("@", 1)
    return (name[:2] + "***@" + domain) if name else email


def _host_allowed(host: str | None, port: int) -> bool:
    value = (host or "").strip().lower()
    return value in {f"127.0.0.1:{port}", f"localhost:{port}"}


def _origin_allowed(origin: str | None, host: str | None) -> bool:
    if not origin:
        return True
    try:
        parsed = urlparse(origin)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    return bool(host) and parsed.netloc.lower() == host.lower()


def _json_content_type_ok(value: str | None) -> bool:
    return (value or "").split(";")[0].strip().lower() == "application/json"


APP = App()


class Handler(BaseHTTPRequestHandler):
    server_version = "PS5HunterUI/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def do_GET(self):
        if not _host_allowed(self.headers.get("Host"), self.server.server_address[1]):
            self._json({"ok": False, "error": "Host 不在白名單"}, 403)
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                html = UI_PATH.read_text(encoding="utf-8")
            except Exception:
                html = "<h1>UI 檔案缺失</h1>"
            html = html.replace("__CSRF_TOKEN__", CSRF_TOKEN)
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/state":
            self._json({"config": APP.config, "status": APP.status()})
            return
        if path == "/api/login/status":
            self._json(APP.login)
            return
        if path.startswith("/assets/"):
            name = path[len("/assets/"):]
            if "/" in name or "\\" in name or ".." in name:
                self._json({"error": "not found"}, 404)
                return
            target = ASSETS_DIR / name
            if target.is_file():
                ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                self._send(200, target.read_bytes(), ctype)
                return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not _host_allowed(self.headers.get("Host"), self.server.server_address[1]):
            self._json({"ok": False, "error": "Host 不在白名單"}, 403)
            return
        if not _origin_allowed(self.headers.get("Origin"), self.headers.get("Host")):
            self._json({"ok": False, "error": "拒絕跨站請求"}, 403)
            return
        if not _json_content_type_ok(self.headers.get("Content-Type")):
            self._json({"ok": False, "error": "Content-Type 必須是 application/json"}, 403)
            return
        token = self.headers.get("X-CSRF-Token") or ""
        if not secrets.compare_digest(token, CSRF_TOKEN):
            self._json({"ok": False, "error": "CSRF token 無效"}, 403)
            return
        path = urlparse(self.path).path
        data = self._body()
        try:
            if path == "/api/save":
                cfg = data.get("config") or {}
                if not isinstance(cfg, dict):
                    raise ValueError("config 格式錯誤")
                APP.config = cfg
                save_config(cfg)
                synced = sync_autostart(bool(cfg.get("autostart")))
                APP.log("設定已儲存" + ("（已設定開機自動啟動）" if cfg.get("autostart") else ""))
                self._json({"ok": True, "autostart": synced})
            elif path == "/api/login":
                self._json(APP.start_login())
            elif path == "/api/discover":
                self._json(APP.discover())
            elif path == "/api/logout":
                self._json(APP.logout())
            elif path == "/api/check-login":
                self._json(APP.check_login())
            elif path == "/api/bot":
                self._json(APP.bot_status())
            elif path == "/api/save-bot":
                self._json(APP.save_bot(data))
            elif path == "/api/clear-bot":
                self._json(APP.clear_bot())
            elif path == "/api/export-logs":
                self._json(APP.export_logs())
            elif path == "/api/open-logs":
                self._json(APP.open_logs())
            elif path == "/api/channels":
                self._json(APP.channels_status())
            elif path == "/api/save-channels":
                self._json(APP.save_channels(data))
            elif path == "/api/test-channel":
                self._json(APP.test_channel(str(data.get("channel") or "")))
            elif path == "/api/price-history":
                self._json(APP.price_history())
            elif path == "/api/price-survey/start":
                self._json(APP.start_price_survey(str(data.get("rule") or "")))
            elif path == "/api/price-survey/status":
                self._json(APP.survey_status())
            elif path == "/api/update/check":
                self._json(APP.check_update())
            elif path == "/api/update/skip":
                self._json(APP.skip_update(str(data.get("version") or "")))
            elif path == "/api/update/open":
                self._json(APP.open_update_page())
            elif path == "/api/update/download":
                self._json(APP.download_update())
            elif path == "/api/update/apply":
                self._json(APP.apply_update())
            elif path == "/api/open-app-passwords":
                webbrowser.open("https://myaccount.google.com/apppasswords")
                self._json({"ok": True})
            elif path == "/api/test-text":
                text = str(data.get("text") or "")
                listing_title = str(data.get("listing_title") or "")
                listing_price = str(data.get("listing_price") or "")
                listing_status = str(data.get("listing_status") or "")
                author_comments = data.get("author_comments") or None
                results = []
                for raw in data.get("rules") or []:
                    rule = rule_from_dict(raw)
                    detail = analyze_detail(
                        text,
                        rule,
                        listing_title=listing_title,
                        listing_price=listing_price,
                        listing_status=listing_status,
                        author_comments=author_comments,
                    )
                    detail["name"] = rule.name
                    results.append(detail)
                self._json({"ok": True, "results": results})
            elif path == "/api/fetch-post":
                self._json(APP.fetch_post(str(data.get("url") or "")))
            elif path == "/api/test-email":
                self._json(APP.test_email())
            elif path == "/api/start":
                self._json(APP.start_monitor())
            elif path == "/api/stop":
                self._json(APP.stop_monitor())
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": str(exc)}, 400)


def _open_window(url: str) -> None:
    """用內建的 WebView2 原生視窗開啟 UI；失敗才退回預設瀏覽器。"""
    if os.environ.get("PS5_UI_NO_BROWSER"):
        return
    bundle = Path(getattr(sys, "_MEIPASS", ROOT))
    host_dir = bundle / "native_host"
    host = host_dir / "LowPriceHunterHost.exe"
    if host.is_file():
        try:
            subprocess.Popen(
                [
                    str(host),
                    url,
                    str(bundle / "assets" / "logo.ico"),
                    str(DATA / "webview_profile"),
                ],
                cwd=str(host_dir),
                close_fds=True,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
            return
        except Exception:
            pass
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main(port: int = 8765):
    url = f"http://127.0.0.1:{port}/"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        # 已經有另一個 UI 在跑，直接打開它
        _open_window(url)
        print(f"UI already running: {url}")
        return
    server.daemon_threads = True
    print(f"Low Price Hunter UI: {url}")
    _open_window(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
