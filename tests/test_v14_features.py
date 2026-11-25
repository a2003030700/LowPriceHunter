import unittest

from ps5hunter.remote import (
    RemoteCommand,
    is_in_quiet_hours,
    is_seller_blocked,
    parse_blocked_sellers,
    parse_remote_command,
)


class QuietHoursTest(unittest.TestCase):
    def test_same_day_window(self):
        self.assertTrue(is_in_quiet_hours(1, 1, 7))
        self.assertTrue(is_in_quiet_hours(6, 1, 7))
        self.assertFalse(is_in_quiet_hours(7, 1, 7))
        self.assertFalse(is_in_quiet_hours(0, 1, 7))

    def test_overnight_window(self):
        self.assertTrue(is_in_quiet_hours(23, 23, 7))
        self.assertTrue(is_in_quiet_hours(0, 23, 7))
        self.assertTrue(is_in_quiet_hours(6, 23, 7))
        self.assertFalse(is_in_quiet_hours(7, 23, 7))
        self.assertFalse(is_in_quiet_hours(22, 23, 7))

    def test_equal_hours_disabled(self):
        self.assertFalse(is_in_quiet_hours(3, 3, 3))


class SellerBlacklistTest(unittest.TestCase):
    def test_parse_and_dedupe(self):
        sellers = parse_blocked_sellers("小明電玩, 大頭, 小明電玩\n阿華二手；  ")
        self.assertEqual(sellers, ["小明電玩", "大頭", "阿華二手"])

    def test_match_case_insensitive_substring(self):
        blocked = ["阿明電玩", "prostore"]
        self.assertEqual(is_seller_blocked("阿明電玩工作室", blocked), "阿明電玩")
        self.assertEqual(is_seller_blocked("ProStore TW", blocked), "prostore")
        self.assertIsNone(is_seller_blocked("陳大文", blocked))
        self.assertIsNone(is_seller_blocked("", blocked))


class RemoteCommandTest(unittest.TestCase):
    def test_basic_commands(self):
        self.assertEqual(parse_remote_command("/status"), RemoteCommand("status"))
        self.assertEqual(parse_remote_command("/status@MyBot"), RemoteCommand("status"))
        self.assertEqual(parse_remote_command("/狀態"), RemoteCommand("status"))
        self.assertEqual(parse_remote_command("/pause"), RemoteCommand("pause"))
        self.assertEqual(parse_remote_command("/暫停"), RemoteCommand("pause"))
        self.assertEqual(parse_remote_command("/resume"), RemoteCommand("resume"))
        self.assertEqual(parse_remote_command("/開始"), RemoteCommand("resume"))
        self.assertEqual(parse_remote_command("/help"), RemoteCommand("help"))

    def test_price_and_block(self):
        self.assertEqual(parse_remote_command("/price 11000"), RemoteCommand("price", "", "11000"))
        self.assertEqual(parse_remote_command("/price PS5 10500"), RemoteCommand("price", "PS5", "10500"))
        self.assertEqual(parse_remote_command("/block 小明電玩 台北"), RemoteCommand("block", "小明電玩 台北"))
        self.assertIsNone(parse_remote_command("一般聊天訊息"))
        self.assertIsNone(parse_remote_command("/unknown"))


if __name__ == "__main__":
    unittest.main()
