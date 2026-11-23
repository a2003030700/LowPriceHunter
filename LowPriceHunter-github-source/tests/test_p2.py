import tempfile
import unittest
from pathlib import Path

from ps5hunter.db import Store
from ps5hunter.notifier import build_digest_message


class PriceHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_pending_rule(self):
        self.store.remember("k", "G", "u", 5000, "text", False, rule_name="PS5")
        rows = self.store.pending()
        self.assertEqual(rows[0][5], "PS5")

    def test_price_history(self):
        self.store.record_price("k1", "PS5", "G", 5000, 6000)
        self.store.record_price("k2", "PS5", "G", 4000, None)
        summary = self.store.price_summary()
        self.assertEqual(summary[0][0], "PS5")
        self.assertEqual(summary[0][1], 2)
        self.assertEqual(summary[0][2], 4000)
        self.assertEqual(summary[0][4], 5000)
        self.assertEqual(len(self.store.recent_prices()), 2)


class DigestGroupTest(unittest.TestCase):
    def test_grouped_by_rule(self):
        items = [
            {"key": "1", "group": "A", "url": "u1", "price": 5000, "text": "t1", "rule": "PS5"},
            {"key": "2", "group": "B", "url": "u2", "price": 15000, "text": "t2", "rule": "iPhone"},
        ]
        msg = build_digest_message(items, "bot@x.com", "to@y.com", "2026-11-22")
        body = msg.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("【PS5】", body)
        self.assertIn("【iPhone】", body)


if __name__ == "__main__":
    unittest.main()
