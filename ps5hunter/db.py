"""SQLite 去重資料庫：記錄看過的貼文，避免重複通知。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS posts (
                key        TEXT PRIMARY KEY,
                group_name TEXT,
                url        TEXT,
                price      INTEGER,
                text       TEXT,
                found_at   TEXT,
                notified   INTEGER DEFAULT 0,
                rule       TEXT
            )
            """
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cursors (
                group_id     TEXT PRIMARY KEY,
                last_post_id TEXT,
                last_seen_ts TEXT,
                updated_at   TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS global_seen (
                key      TEXT PRIMARY KEY,
                sources  TEXT,
                seen_at  TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                post_key   TEXT,
                rule       TEXT,
                group_name TEXT,
                price      INTEGER,
                price_high INTEGER,
                created_at TEXT
            )
            """
        )
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(posts)")}
        if "rule" not in cols:
            self.conn.execute("ALTER TABLE posts ADD COLUMN rule TEXT")
        self.conn.commit()

    # ------------------------------------------------------------------
    def seen(self, key: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM posts WHERE key = ?", (key,))
        return cur.fetchone() is not None

    def remember(
        self,
        key: str,
        group_name: str,
        url: str,
        price: int | None,
        text: str,
        notified: bool,
        rule_name: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT OR REPLACE INTO posts (key, group_name, url, price, text, found_at, notified, rule)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (key, group_name, url, price, text[:4000], now, 1 if notified else 0, rule_name),
        )
        self.conn.commit()

    def is_fresh(self, scope: str = "default") -> bool:
        """是否還沒跑過第一次（用來建立基準線）。"""
        cur = self.conn.execute("SELECT v FROM meta WHERE k = ?", (f"baseline:{scope}",))
        return cur.fetchone() is None

    def pending(self, limit: int = 100) -> list[tuple]:
        """還沒通知的符合貼文（每日摘要用）。"""
        cur = self.conn.execute(
            "SELECT key, group_name, url, price, text, rule FROM posts"
            " WHERE notified = 0 ORDER BY found_at DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()

    def mark_notified(self, keys: list[str]) -> None:
        self.conn.executemany(
            "UPDATE posts SET notified = 1 WHERE key = ?", [(k,) for k in keys]
        )
        self.conn.commit()

    def record_price(
        self,
        post_key: str,
        rule: str,
        group_name: str,
        price: int,
        price_high: int | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO price_history (post_key, rule, group_name, price, price_high, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (post_key, rule, group_name, price, price_high, now),
        )
        self.conn.commit()

    def price_summary(self) -> list[tuple]:
        return self.conn.execute(
            "SELECT rule, COUNT(*), MIN(price), AVG(price), MAX(price)"
            " FROM price_history GROUP BY rule ORDER BY COUNT(*) DESC"
        ).fetchall()

    def recent_prices(self, limit: int = 50) -> list[tuple]:
        return self.conn.execute(
            "SELECT rule, group_name, price, price_high, created_at"
            " FROM price_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def set_baseline(self, scope: str = "default") -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (f"baseline:{scope}", now)
        )
        self.conn.commit()

    # ---------------------------------------------------------------- cursors
    def get_cursor(self, group_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT last_post_id, last_seen_ts FROM cursors WHERE group_id = ?",
            (group_id,),
        ).fetchone()
        if not row:
            return None
        return {"last_post_id": row[0], "last_seen_ts": row[1]}

    def set_cursor(
        self, group_id: str, last_post_id: str | None, last_seen_ts: str | None
    ) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT OR REPLACE INTO cursors (group_id, last_post_id, last_seen_ts, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (group_id, last_post_id, last_seen_ts, now),
        )
        self.conn.commit()

    def seen_global(self, key: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM global_seen WHERE key = ?", (key,))
        return cur.fetchone() is not None

    def claim_global(self, key: str, sources: list[str]) -> bool:
        """原子搶佔全局去重鍵；回傳 True 代表這次由我處理。"""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO global_seen (key, sources, seen_at) VALUES (?, ?, ?)",
            (key, "、".join(sources), now),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def release_global(self, key: str) -> None:
        self.conn.execute("DELETE FROM global_seen WHERE key = ?", (key,))
        self.conn.commit()

    def remember_global(self, key: str, sources: list[str]) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT OR REPLACE INTO global_seen (key, sources, seen_at) VALUES (?, ?, ?)",
            (key, "、".join(sources), now),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
