import unittest

from ps5hunter.parser import analyze, find_prices


class PriceTest(unittest.TestCase):
    def test_plain_number_with_sell_context(self):
        prices = find_prices("售 PS5 光碟版 8500 台北面交")
        self.assertEqual(prices[0].amount, 8500)

    def test_currency_symbol(self):
        self.assertEqual(find_prices("PS5 slim NT$ 8,500 全新")[0].amount, 8500)

    def test_yuan_suffix(self):
        self.assertEqual(find_prices("PS5 數位版 9000元 含運")[0].amount, 9000)

    def test_wan(self):
        self.assertEqual(find_prices("PS5 售 1.2萬")[0].amount, 12000)

    def test_qian(self):
        self.assertEqual(find_prices("PS5 主機 9千 出清")[0].amount, 9000)

    def test_lowest_price_wins(self):
        prices = find_prices("PS5 原價 17580 售 12000 可議")
        self.assertEqual(prices[0].amount, 12000)

    def test_year_is_not_price(self):
        prices = find_prices("2025年出廠 PS5 光碟版 售 15000")
        self.assertEqual(prices[0].amount, 15000)

    def test_storage_is_not_price(self):
        prices = find_prices("PS5 1TB 光碟版 售 13500")
        self.assertEqual(prices[0].amount, 13500)


class AnalyzeTest(unittest.TestCase):
    def test_deal(self):
        a = analyze("售 PS5 光碟版 9成新 售 8500 台北面交", 10000)
        self.assertTrue(a.is_deal)
        self.assertEqual(a.price, 8500)

    def test_ps5_with_space(self):
        a = analyze("PS 5 主機便宜賣 9500", 10000)
        self.assertTrue(a.is_deal)

    def test_playstation5(self):
        a = analyze("PlayStation 5 主機 降價 9999 元", 10000)
        self.assertTrue(a.is_deal)

    def test_controller_excluded(self):
        a = analyze("PS5 手把 GTA6 控制器 $4000", 10000)
        self.assertFalse(a.is_deal)

    def test_games_excluded(self):
        a = analyze("PS5 遊戲片 GT7 $1200 劍星 $1000 仁王 $800", 10000)
        self.assertFalse(a.is_deal)

    def test_multiple_prices_picks_console(self):
        a = analyze("PS5 主機 售 12000 另售手把 1800", 20000)
        self.assertTrue(a.is_deal)
        self.assertEqual(a.price, 12000)

    def test_original_price_not_used(self):
        a = analyze("PS5 光碟版 原價 17580 售 12000", 20000)
        self.assertEqual(a.price, 12000)

    def test_model_number_not_price(self):
        a = analyze("PS5 1218A光碟版 有盒有配件 $12500", 20000)
        self.assertEqual(a.price, 12500)

    def test_games_low_price_excluded(self):
        a = analyze("PS5 主機 遊戲片 浪人崛起 650元 劍星 900元", 10000)
        self.assertFalse(a.is_deal)

    def test_games_with_console_mention_excluded(self):
        a = analyze("PS4遊戲片（PS5主機可以玩）七片 $4000", 10000)
        self.assertFalse(a.is_deal)

    def test_game_list_excluded(self):
        a = analyze("【售】PS5 蜘蛛人 $550 2.PS5 蜘蛛人2 $1000 3.PS5 GTA5 $550", 10000)
        self.assertFalse(a.is_deal)

    def test_console_without_keyword_near_price(self):
        a = analyze("售 PS5 2023年購入 少玩 9000", 10000)
        self.assertTrue(a.is_deal)

    def test_buying_bracket_excluded(self):
        a = analyze("陳翰 分享對象：私密社團【收】【平台】PS5【品名價格】PS5 預算 $10000", 10000)
        self.assertFalse(a.is_deal)

    def test_buying_collect_one_excluded(self):
        a = analyze("收一台PS5 slim 希望光碟版 數位版也可以 預算15000左右", 20000)
        self.assertFalse(a.is_deal)

    def test_sale_still_works(self):
        a = analyze("售 PS5 主機 8500 台南面交", 10000)
        self.assertTrue(a.is_deal)

    def test_reserved_post_excluded(self):
        a = analyze("PS5 主機 暫售 8500", 10000)
        self.assertFalse(a.is_deal)

    def test_above_threshold(self):
        a = analyze("PS5 光碟版 售 15000", 10000)
        self.assertFalse(a.is_deal)
        self.assertEqual(a.price, 15000)

    def test_wanted_post_excluded(self):
        a = analyze("徵 PS5 一台 預算 8000", 10000)
        self.assertTrue(a.is_ps5)
        self.assertFalse(a.is_deal)

    def test_sold_post_excluded(self):
        a = analyze("PS5 已售出 謝謝 8500", 10000)
        self.assertFalse(a.is_deal)

    def test_not_ps5(self):
        a = analyze("售 PS4 主機 5000", 10000)
        self.assertFalse(a.is_ps5)

    def test_threshold_20000(self):
        a = analyze("PS5 光碟版 售 18500", 20000)
        self.assertTrue(a.is_deal)


if __name__ == "__main__":
    unittest.main()
