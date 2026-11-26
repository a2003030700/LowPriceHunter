"""SQLite 去重與歷史資料庫：記錄看過的貼文、命中紀錄、被攔截稽核日誌與價格歷史。"""

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
                rule       TEXT,
                author     TEXT DEFAULT '',
                image_url  TEXT DEFAULT '',
                status     TEXT DEFAULT ''
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
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                post_key   TEXT,
                group_name TEXT,
                author     TEXT,
                url        TEXT,
                rule       TEXT,
                reason     TEXT,
                price      INTEGER,
                snippet    TEXT,
                image_url  TEXT,
                created_at TEXT
            )
            """
        )
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(posts)")}
        if "rule" not in cols:
            self.conn.execute("ALTER TABLE posts ADD COLUMN rule TEXT")
        if "author" not in cols:
            self.conn.execute("ALTER TABLE posts ADD COLUMN author TEXT DEFAULT ''")
        if "image_url" not in cols:
            self.conn.execute("ALTER TABLE posts ADD COLUMN image_url TEXT DEFAULT ''")
        if "status" not in cols:
            self.conn.execute("ALTER TABLE posts ADD COLUMN status TEXT DEFAULT ''")
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
        author: str = "",
        image_url: str = "",
        status: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT OR REPLACE INTO posts"
            " (key, group_name, url, price, text, found_at, notified, rule, author, image_url, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key, group_name, url, price, text[:4000], now,
                1 if notified else 0, rule_name,
                (author or "")[:80], (image_url or "")[:1000], (status or "")[:40],
            ),
        )
        self.conn.commit()

    def set_deal_status(self, key: str, status: str) -> None:
        self.conn.execute(
            "UPDATE posts SET status = ? WHERE key = ?",
            ((status or "")[:40], key),
        )
        self.conn.commit()

    def recent_deals(self, limit: int = 50) -> list[dict]:
        """回傳最近命中的低價貼文清單（供 UI 儀表板顯示）。"""
        rows = self.conn.execute(
            "SELECT key, group_name, url, price, text, found_at, rule, author, image_url, status"
            " FROM posts WHERE price IS NOT NULL AND rule IS NOT NULL"
            " ORDER BY found_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "key": r[0],
                "group": r[1] or "",
                "url": r[2] or "",
                "price": r[3],
                "text": (r[4] or "")[:400],
                "found_at": r[5] or "",
                "rule": r[6] or "",
                "author": r[7] or "",
                "image_url": r[8] or "",
                "status": r[9] or "",
            }
            for r in rows
        ]

    def recent_notified_deals(self, hours: int = 24, limit: int = 200) -> list[dict]:
        """回傳指定小時內已通知的貼文清單（供跨社團／同賣家去重使用）。"""
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, hours))).isoformat(
            timespec="seconds"
        )
        rows = self.conn.execute(
            "SELECT key, group_name, url, price, text, found_at, rule, author"
            " FROM posts"
            " WHERE notified = 1 AND price IS NOT NULL AND rule IS NOT NULL"
            " AND found_at >= ?"
            " ORDER BY found_at DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
        return [
            {
                "key": r[0],
                "group": r[1] or "",
                "url": r[2] or "",
                "price": r[3],
                "text": (r[4] or "")[:600],
                "found_at": r[5] or "",
                "rule": r[6] or "",
                "author": r[7] or "",
            }
            for r in rows
        ]

    def record_blocked(
        self,
        post_key: str,
        group_name: str,
        author: str,
        url: str,
        rule: str,
        reason: str,
        price: int | None,
        snippet: str,
        image_url: str = "",
        keep_max: int = 300,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO audit_log (post_key, group_name, author, url, rule, reason, price, snippet, image_url, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                post_key, group_name, (author or "")[:80], url or "", rule or "",
                (reason or "")[:120], price, (snippet or "")[:360], (image_url or "")[:1000], now,
            ),
        )
        self.conn.execute(
            "DELETE FROM audit_log WHERE id NOT IN ("
            "  SELECT id FROM audit_log ORDER BY id DESC LIMIT ?"
            ")",
            (keep_max,),
        )
        self.conn.commit()

    def recent_blocked(self, limit: int = 60) -> list[dict]:
        rows = self.conn.execute(
            "SELECT post_key, group_name, author, url, rule, reason, price, snippet, image_url, created_at"
            " FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "key": r[0] or "",
                "group": r[1] or "",
                "author": r[2] or "",
                "url": r[3] or "",
                "rule": r[4] or "",
                "reason": r[5] or "",
                "price": r[6],
                "text": r[7] or "",
                "image_url": r[8] or "",
                "created_at": r[9] or "",
            }
            for r in rows
        ]

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
