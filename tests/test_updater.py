import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from ps5hunter.updater import (
    UpdateError,
    build_apply_update_script,
    choose_zip_asset,
    check_for_update,
    extract_update_package,
    is_newer,
    parse_version,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class VersionTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(parse_version("1.2"), (1, 2))
        self.assertEqual(parse_version(""), (0,))

    def test_newer(self):
        self.assertTrue(is_newer("1.2.0", "1.1.9"))
        self.assertTrue(is_newer("v1.10.0", "1.9.9"))
        self.assertFalse(is_newer("1.1.0", "1.1.0"))
        self.assertFalse(is_newer("1.0.9", "1.1.0"))


class AssetTest(unittest.TestCase):
    def test_prefers_update_zip(self):
        assets = [
            {"name": "notes.txt", "browser_download_url": "n"},
            {"name": "LowPriceHunter-portable.zip", "browser_download_url": "p"},
            {"name": "LowPriceHunter-update.zip", "browser_download_url": "u"},
        ]
        self.assertEqual(choose_zip_asset(assets)["browser_download_url"], "u")

    def test_no_zip(self):
        self.assertIsNone(choose_zip_asset([{"name": "notes.txt"}]))


class CheckTest(unittest.TestCase):
    def test_available(self):
        payload = {
            "tag_name": "v1.4.0",
            "html_url": "https://github.com/a/b/releases/tag/v1.4.0",
            "body": "notes",
            "published_at": "2026-11-23T00:00:00Z",
            "assets": [
                {"name": "LowPriceHunter-update.zip", "browser_download_url": "https://x/y.zip"}
            ],
        }
        with patch("ps5hunter.updater.urllib.request.urlopen", return_value=FakeResponse(payload)):
            info = check_for_update("a/b")
        self.assertTrue(info.available)
        self.assertEqual(info.latest, "1.4.0")
        self.assertEqual(info.asset_url, "https://x/y.zip")

    def test_up_to_date(self):
        payload = {"tag_name": "v1.1.0", "html_url": "u", "assets": []}
        with patch("ps5hunter.updater.urllib.request.urlopen", return_value=FakeResponse(payload)):
            info = check_for_update("a/b")
        self.assertFalse(info.available)

    def test_invalid_repo(self):
        with self.assertRaises(UpdateError):
            check_for_update("not-a-repo")


class AutoApplyUpdateTest(unittest.TestCase):
    def test_extract_strips_data_and_logs_and_finds_nested_exe(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            zpath = root / "pkg.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.writestr("LowPriceHunter/LowPriceHunter.exe", b"MZ")
                zf.writestr("LowPriceHunter/_internal/a.txt", b"ok")
                zf.writestr("LowPriceHunter/data/app_config.json", b"{}")
                zf.writestr("LowPriceHunter/logs/ui.log", b"x")
            app_dir = extract_update_package(zpath, root / "out")
            self.assertTrue((app_dir / "LowPriceHunter.exe").is_file())
            self.assertTrue((app_dir / "_internal" / "a.txt").is_file())
            self.assertFalse((app_dir / "data").exists())
            self.assertFalse((app_dir / "logs").exists())

    def test_extract_rejects_zipslip_and_missing_exe(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bad_slip = root / "slip.zip"
            with zipfile.ZipFile(bad_slip, "w") as zf:
                zf.writestr("../evil.txt", b"bad")
            with self.assertRaises(UpdateError):
                extract_update_package(bad_slip, root / "out1")

            no_exe = root / "noexe.zip"
            with zipfile.ZipFile(no_exe, "w") as zf:
                zf.writestr("readme.txt", b"hi")
            with self.assertRaises(UpdateError):
                extract_update_package(no_exe, root / "out2")

    def test_build_apply_script_preserves_data_and_cleans_stage(self):
        script = build_apply_update_script(
            Path(r"C:\Apps\LowPriceHunter"),
            Path(r"C:\Temp\lph\extracted"),
            Path(r"C:\Temp\lph"),
            pid=1234,
            desktop_dir=Path(r"C:\Users\me\Desktop"),
        )
        self.assertIn("LowPriceHunter.exe", script)
        self.assertIn("LowPriceHunterHost", script)
        self.assertIn("Remove-Item -Path $StageDir -Recurse -Force", script)
        self.assertIn("Start-Process -FilePath", script)
        self.assertNotIn("Remove-Item (Join-Path $AppDir 'data')", script)


if __name__ == "__main__":
    unittest.main()
