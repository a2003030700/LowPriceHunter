import json
import sys
import tempfile
import unittest
from pathlib import Path

from ps5hunter import dpapi
from ps5hunter.botstore import BotStore


@unittest.skipUnless(sys.platform == "win32", "DPAPI 僅支援 Windows")
class BotStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = BotStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_available(self):
        self.assertTrue(dpapi.available())

    def test_roundtrip_encrypted(self):
        self.store.save({"user": "bot@example.com", "app_password": "super-secret-123"})
        self.assertTrue(self.store.enc.exists())
        self.assertFalse(self.store.plain.exists())
        self.assertNotIn(b"super-secret-123", self.store.enc.read_bytes())
        loaded = self.store.load()
        self.assertEqual(loaded["user"], "bot@example.com")
        self.assertEqual(loaded["app_password"], "super-secret-123")

    def test_plaintext_migration(self):
        self.store.plain.parent.mkdir(parents=True, exist_ok=True)
        self.store.plain.write_text(
            json.dumps({"user": "bot@example.com", "app_password": "old-secret"}),
            encoding="utf-8",
        )
        loaded = self.store.load()
        self.assertEqual(loaded["app_password"], "old-secret")
        self.assertTrue(self.store.enc.exists())
        self.assertFalse(self.store.plain.exists())
        self.assertNotIn(b"old-secret", self.store.enc.read_bytes())

    def test_clear(self):
        self.store.save({"user": "bot@example.com", "app_password": "x"})
        self.store.clear()
        self.assertFalse(self.store.enc.exists())
        self.assertFalse(self.store.plain.exists())
        self.assertEqual(self.store.load(), {})


if __name__ == "__main__":
    unittest.main()
