import sys
import tempfile
import unittest
from pathlib import Path

from ps5hunter.channels import ChannelStore
from ps5hunter.notifier import NotificationError, build_text_notification, send_telegram


@unittest.skipUnless(sys.platform == "win32", "DPAPI 僅支援 Windows")
class ChannelStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ChannelStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_encrypted(self):
        self.store.save({"telegram": {"bot_token": "secret-token", "chat_id": "123"}})
        self.assertNotIn(b"secret-token", self.store.enc.read_bytes())
        data = self.store.load()
        self.assertEqual(data["telegram"]["chat_id"], "123")
        self.assertEqual(data["telegram"]["bot_token"], "secret-token")


class TextNotificationTest(unittest.TestCase):
    def test_sources_and_risk(self):
        text = build_text_notification(
            "PS5", 5000, "A", "https://example.com", "snippet",
            sources=["A", "B"], risk_words=["先匯款"],
        )
        self.assertIn("NT$ 5,000", text)
        self.assertIn("共 2 個", text)
        self.assertIn("先匯款", text)


class TelegramGuardTest(unittest.TestCase):
    def test_missing_token_raises(self):
        with self.assertRaises(NotificationError):
            send_telegram("", "", "x")


if __name__ == "__main__":
    unittest.main()
