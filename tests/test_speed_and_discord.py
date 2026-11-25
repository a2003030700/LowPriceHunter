"""v1.3.2 速度優化與 Discord Embed 推播測試。"""

import unittest
from unittest.mock import patch

from ps5hunter.notifier import NotificationError, build_discord_embed, send_discord


class DiscordEmbedTest(unittest.TestCase):
    def test_build_discord_embed_normal_and_split_sale(self):
        emb = build_discord_embed(
            "PS5", 9500, "【PS5】臺灣買賣交流區",
            "https://www.facebook.com/groups/486139604907689/posts/123/",
            "PS5 Slim 光碟版 拆售主機 9500 台北面交",
            is_split_sale=True,
        )
        self.assertIn("NT$ 9,500", emb["title"])
        self.assertIn("拆售價", emb["title"])
        self.assertEqual(emb["color"], 0x10B981)
        self.assertEqual(emb["url"], "https://www.facebook.com/groups/486139604907689/posts/123/")
        self.assertTrue(any("快速開啟貼文" in f["name"] for f in emb["fields"]))

    def test_build_discord_embed_with_risk_words(self):
        emb = build_discord_embed(
            "PS5", 8000, "G1", "https://example.com/1", "急售 先匯款",
            risk_words=["急售", "先匯款"],
        )
        self.assertEqual(emb["color"], 0xF59E0B)
        self.assertTrue(emb["title"].startswith("⚠️"))
        self.assertTrue(any("高風險" in f["name"] for f in emb["fields"]))

    def test_send_discord_includes_embed(self):
        captured = {}

        def fake_post(url, payload, timeout=20):
            captured["url"] = url
            captured["payload"] = payload

        emb = build_discord_embed("PS5", 9000, "G", "https://example.com", "hi")
        with patch("ps5hunter.notifier._post_json", side_effect=fake_post):
            send_discord("https://discord.com/api/webhooks/1/abc", "🔔 **PS5** NT$ 9,000", embed=emb)

        self.assertEqual(captured["url"], "https://discord.com/api/webhooks/1/abc")
        self.assertIn("embeds", captured["payload"])
        self.assertEqual(len(captured["payload"]["embeds"]), 1)
        self.assertIn("PS5", captured["payload"]["content"])

    def test_send_discord_missing_webhook_raises(self):
        with self.assertRaises(NotificationError):
            send_discord("", "hi")


if __name__ == "__main__":
    unittest.main()
