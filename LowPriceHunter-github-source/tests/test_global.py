import tempfile
import unittest
from pathlib import Path

from ps5hunter.db import Store
from ps5hunter.generic import DEFAULT_EXCLUDES, find_risk_words
from ps5hunter.notifier import build_generic_message
from ps5hunter.scraper import Post
from ps5hunter.webui import _global_key


class GlobalSeenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_global_seen(self):
        self.assertFalse(self.store.seen_global("p:1"))
        self.store.remember_global("p:1", ["A", "B"])
        self.assertTrue(self.store.seen_global("p:1"))


class GlobalKeyTest(unittest.TestCase):
    def test_post_id(self):
        post = Post(key="k", group_name="A", group_id="1", text="x", post_id="123", url=None)
        self.assertEqual(_global_key(post), "p:123")

    def test_url_post_id(self):
        post = Post(
            key="k", group_name="A", group_id="1", text="x", post_id=None,
            url="https://www.facebook.com/groups/1/posts/456/?ref=1",
        )
        self.assertEqual(_global_key(post), "p:456")

    def test_text_hash(self):
        post = Post(key="k", group_name="A", group_id="1", text="hello", post_id=None, url=None)
        self.assertTrue(_global_key(post).startswith("t:"))


class RiskTest(unittest.TestCase):
    def test_default_excludes(self):
        self.assertIn("已售出", DEFAULT_EXCLUDES)
        self.assertIn("已成交", DEFAULT_EXCLUDES)
        self.assertIn("sold", DEFAULT_EXCLUDES)

    def test_risk_words(self):
        self.assertEqual(find_risk_words("先匯款 可代購"), ["先匯款", "代購"])
        self.assertEqual(find_risk_words("正常賣場"), [])


class MessageSourcesTest(unittest.TestCase):
    def test_sources_and_risk(self):
        msg = build_generic_message(
            "PS5", 5000, "A", "https://example.com", "snippet", "bot@x.com", "to@y.com",
            sources=["A", "B", "C"], risk_words=["預付"],
        )
        self.assertIn("3 個社團", msg["Subject"])
        body = msg.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("共 3 個", body)
        self.assertIn("高風險詞：預付", body)


if __name__ == "__main__":
    unittest.main()
