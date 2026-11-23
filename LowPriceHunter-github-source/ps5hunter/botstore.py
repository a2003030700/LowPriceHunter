"""機器人 Gmail 憑證的儲存：優先 DPAPI 加密，舊明文自動遷移。"""

from __future__ import annotations

import json
from pathlib import Path

from . import dpapi


class BotStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.plain = self.data_dir / "bot_email.json"
        self.enc = self.data_dir / "bot_email.dpapi"

    def load(self) -> dict:
        if self.enc.exists():
            raw = dpapi.unprotect(self.enc.read_bytes())
            bot = json.loads(raw.decode("utf-8"))
            return bot if isinstance(bot, dict) else {}
        if self.plain.exists():
            bot = json.loads(self.plain.read_text(encoding="utf-8-sig"))
            if isinstance(bot, dict) and bot.get("user") and bot.get("app_password"):
                self.save(bot)  # 寫入加密檔後刪除明文
            return bot if isinstance(bot, dict) else {}
        return {}

    def save(self, bot: dict) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(bot, ensure_ascii=False).encode("utf-8")
        self.enc.write_bytes(dpapi.protect(raw))
        if self.plain.exists():
            self.plain.unlink()

    def clear(self) -> None:
        for path in (self.enc, self.plain):
            if path.exists():
                path.unlink()
