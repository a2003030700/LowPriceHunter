"""日夜勿擾排程、賣家黑名單比對與遠端指令解析（Telegram / Discord 雙向控制）。"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass

_UA = "LowPriceHunter/1.4.0 (+https://github.com/a2003030700/LowPriceHunter)"


def is_in_quiet_hours(hour: int, start_hour: int, end_hour: int) -> bool:
    """判斷目前小時是否落在勿擾時段（支援跨午夜，例如 23 -> 7 或 1 -> 7）。"""
    h = int(hour) % 24
    s = int(start_hour) % 24
    e = int(end_hour) % 24
    if s == e:
        return False
    if s < e:
        return s <= h < e
    return h >= s or h < e


def parse_blocked_sellers(value) -> list[str]:
    """把設定中的黑名單賣家（陣列或逗號／換行字串）整理成去重清單。"""
    if isinstance(value, (list, tuple)):
        parts = [str(x).strip() for x in value]
    else:
        parts = re.split(r"[,，;；\n\r]+", str(value or ""))
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        cleaned = p.strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            out.append(cleaned)
    return out


def is_seller_blocked(author: str, blocked_sellers: list[str]) -> str | None:
    """若貼文作者命中賣家黑名單，回傳命中的黑名單名稱；否則回傳 None。"""
    if not author or not blocked_sellers:
        return None
    low = author.strip().lower()
    for name in blocked_sellers:
        n = name.strip().lower()
        if n and (low == n or n in low):
            return name
    return None


@dataclass(frozen=True)
class RemoteCommand:
    action: str  # "status" | "pause" | "resume" | "price" | "block" | "help"
    arg1: str = ""
    arg2: str = ""


def parse_remote_command(text: str) -> RemoteCommand | None:
    """解析遠端指令（支援 /status、/pause、/resume、/price、/block、/help）。"""
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return None
    parts = raw.split()
    if not parts:
        return None
    cmd = parts[0].split("@", 1)[0].lower()
    if cmd in ("/status", "/ping", "/狀態"):
        return RemoteCommand("status")
    if cmd in ("/pause", "/stop", "/暫停"):
        return RemoteCommand("pause")
    if cmd in ("/resume", "/start", "/恢復", "/開始"):
        return RemoteCommand("resume")
    if cmd in ("/help", "/指令"):
        return RemoteCommand("help")
    if cmd in ("/price", "/預算") and len(parts) >= 2:
        # /price 11000  或  /price PS5 11000
        if len(parts) == 2:
            return RemoteCommand("price", "", parts[1])
        return RemoteCommand("price", parts[1], parts[2])
    if cmd in ("/block", "/黑名單") and len(parts) >= 2:
        return RemoteCommand("block", " ".join(parts[1:]).strip())
    return None


def poll_telegram_updates(token: str, offset: int = 0, timeout: int = 10) -> tuple[list[tuple[str, str]], int]:
    """拉取 Telegram Bot 最新訊息，回傳 [(chat_id, text), ...] 與新的 offset。"""
    if not token:
        return [], offset
    url = f"https://api.telegram.org/bot{token}/getUpdates?timeout=0&limit=20"
    if offset > 0:
        url += f"&offset={offset}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    if not data.get("ok"):
        return [], offset
    messages: list[tuple[str, str]] = []
    next_offset = offset
    for item in data.get("result") or []:
        uid = int(item.get("update_id") or 0)
        if uid >= next_offset:
            next_offset = uid + 1
        msg = item.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id") or "")
        text = str(msg.get("text") or "").strip()
        if chat_id and text:
            messages.append((chat_id, text))
    return messages, next_offset


def poll_discord_messages(
    bot_token: str,
    channel_id: str,
    after_id: str = "",
    timeout: int = 10,
) -> tuple[list[tuple[str, str, str]], str]:
    """拉取 Discord 指定頻道最新訊息，回傳 [(msg_id, author_id, content), ...] 與最後 msg_id。"""
    if not bot_token or not channel_id:
        return [], after_id
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages?limit=15"
    if after_id:
        url += f"&after={after_id}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bot {bot_token}",
            "User-Agent": _UA,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        rows = json.loads(resp.read().decode("utf-8", "replace"))
    if not isinstance(rows, list) or not rows:
        return [], after_id
    # Discord 回傳由新到舊，反轉成由舊到新依序處理
    rows.reverse()
    out: list[tuple[str, str, str]] = []
    last_id = after_id
    for row in rows:
        mid = str(row.get("id") or "")
        author = row.get("author") or {}
        if author.get("bot"):
            if mid:
                last_id = mid
            continue
        content = str(row.get("content") or "").strip()
        aid = str(author.get("id") or "")
        if mid:
            last_id = mid
        if content:
            out.append((mid, aid, content))
    return out, last_id


def send_discord_bot_reply(bot_token: str, channel_id: str, text: str, timeout: int = 15) -> None:
    """透過 Discord Bot 回覆指令執行結果至該頻道。"""
    if not bot_token or not channel_id:
        return
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    body = json.dumps({"content": (text or "")[:1900]}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bot {bot_token}",
            "Content-Type": "application/json",
            "User-Agent": _UA,
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()
