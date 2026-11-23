import tempfile
import unittest
import zipfile
from pathlib import Path

from ps5hunter.webui import _sanitize_config, _sanitize_text, build_log_bundle


class SanitizeTest(unittest.TestCase):
    def test_email_masked(self):
        self.assertEqual(_sanitize_text("寄到 a@b.com 失敗"), "寄到 ***@*** 失敗")

    def test_config_recipients_masked(self):
        cfg = {"email": {"to": ["a@b.com", "c@d.com"]}, "rules": []}
        clean = _sanitize_config(cfg)
        self.assertEqual(clean["email"]["to"], ["***", "***"])
        self.assertEqual(cfg["email"]["to"], ["a@b.com", "c@d.com"])

    def test_config_string_masked(self):
        clean = _sanitize_config({"email": {"to": "a@b.com"}})
        self.assertEqual(clean["email"]["to"], "***")


class BundleTest(unittest.TestCase):
    def test_bundle_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            (logs / "ui.log").write_text("12:00 錯誤 a@b.com\n", encoding="utf-8")
            out = root / "bundle.zip"
            build_log_bundle(logs, out, "連線失敗 a@b.com", {"email": {"to": "a@b.com"}})
            self.assertTrue(out.exists())
            with zipfile.ZipFile(out) as zf:
                self.assertIn("summary.txt", zf.namelist())
                self.assertIn("config.json", zf.namelist())
                self.assertIn("logs/ui.log", zf.namelist())
                for name in zf.namelist():
                    data = zf.read(name).decode("utf-8")
                    self.assertNotIn("a@b.com", data)


if __name__ == "__main__":
    unittest.main()
