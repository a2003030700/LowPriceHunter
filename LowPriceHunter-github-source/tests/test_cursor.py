import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ps5hunter.db import Store
from ps5hunter.scraper import Post
from ps5hunter.webui import _newest_post, parse_recipients


class CursorStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_cursor_missing(self):
        self.assertIsNone(self.store.get_cursor("g1"))

    def test_cursor_roundtrip(self):
        self.store.set_cursor("g1", "123", "2026-01-01T00:00:00+00:00")
        self.assertEqual(
            self.store.get_cursor("g1"),
            {"last_post_id": "123", "last_seen_ts": "2026-01-01T00:00:00+00:00"},
        )

    def test_cursor_update(self):
        self.store.set_cursor("g1", "1", "2026-01-01T00:00:00+00:00")
        self.store.set_cursor("g1", "2", "2026-01-02T00:00:00+00:00")
        self.assertEqual(self.store.get_cursor("g1")["last_post_id"], "2")

    def test_first_run_baseline_flag(self):
        self.assertTrue(self.store.is_fresh())
        self.store.set_baseline()
        self.assertFalse(self.store.is_fresh())

    def test_claim_global_atomic(self):
        self.assertTrue(self.store.claim_global("p:1", ["A"]))
        self.assertFalse(self.store.claim_global("p:1", ["B"]))
        self.store.release_global("p:1")
        self.assertTrue(self.store.claim_global("p:1", ["C"]))


class RecipientsTest(unittest.TestCase):
    def test_split_and_dedup(self):
        self.assertEqual(
            parse_recipients("a@x.com, b@y.com\nc@z.com; a@x.com"),
            ["a@x.com", "b@y.com", "c@z.com"],
        )

    def test_ignore_invalid(self):
        self.assertEqual(parse_recipients(["a@x.com", "bad", "b@y.com"]), ["a@x.com", "b@y.com"])


class NewestPostTest(unittest.TestCase):
    def post(self, post_id, created_at):
        return Post(
            key=post_id,
            group_name="G",
            group_id="1",
            text="text",
            post_id=post_id,
            url=None,
            created_at=created_at,
        )

    def test_skips_older_pinned_post(self):
        pinned = self.post("pinned", datetime(2026, 11, 20, tzinfo=timezone.utc))
        newest = self.post("new", datetime(2026, 11, 23, tzinfo=timezone.utc))
        self.assertEqual(_newest_post([pinned, newest]).post_id, "new")

    def test_falls_back_to_first(self):
        first = self.post("first", None)
        second = self.post("second", None)
        self.assertEqual(_newest_post([first, second]).post_id, "first")


if __name__ == "__main__":
    unittest.main()
