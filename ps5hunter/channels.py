"""通知渠道憑證（Telegram token、Discord webhook）的 DPAPI 儲存。"""

from __future__ import annotations

import json
from pathlib import Path

from . import dpapi


class ChannelStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.enc = self.data_dir / "channels.dpapi"

    def load(self) -> dict:
        if not self.enc.exists():
            return {}
        raw = dpapi.unprotect(self.enc.read_bytes())
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}

    def save(self, data: dict) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.enc.write_bytes(dpapi.protect(json.dumps(data, ensure_ascii=False).encode("utf-8")))

    def clear(self) -> None:
        if self.enc.exists():
            self.enc.unlink()
