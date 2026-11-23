import unittest

from ps5hunter.generic import Rule, analyze_detail


class TesterDetailTest(unittest.TestCase):
    def rule(self, **kw):
        return Rule(
            name="PS5",
            keywords=["PS5"],
            require=["主機"],
            exclude=["遊戲片"],
            min_price=3000,
            max_price=10000,
            price_near_keyword=True,
            near_distance=60,
            reject_multi_item=True,
            **kw,
        )

    def test_matched_spans_and_gates(self):
        text = "售 PS5 主機 5000-6000"
        detail = analyze_detail(text, self.rule())
        self.assertTrue(detail["matched"])
        self.assertEqual(text[slice(*detail["spans"]["keyword"])], "PS5")
        self.assertEqual(text[slice(*detail["spans"]["require"])], "主機")
        self.assertEqual(text[slice(*detail["spans"]["price"])], "5000-6000")
        self.assertEqual(detail["price"], 5000)
        self.assertEqual(detail["price_high"], 6000)
        self.assertTrue(all(g["ok"] for g in detail["gates"]))
        self.assertEqual(detail["gates"][-1]["name"], "結果")
        self.assertLessEqual(detail["price_distance"], detail["near_limit"])

    def test_excluded_word_span(self):
        text = "售 PS5 主機 5000 遊戲片"
        detail = analyze_detail(text, self.rule())
        self.assertFalse(detail["matched"])
        self.assertEqual(text[slice(*detail["spans"]["exclude"])], "遊戲片")
        self.assertFalse(detail["gates"][-1]["ok"])
        self.assertEqual(detail["gates"][-1]["name"], "排除詞")

    def test_missing_require(self):
        text = "售 PS5 5000"
        detail = analyze_detail(text, self.rule())
        self.assertFalse(detail["matched"])
        self.assertIsNone(detail["spans"].get("require"))
        self.assertFalse(detail["gates"][-1]["ok"])
        self.assertEqual(detail["gates"][-1]["name"], "必須包含")

    def test_no_price_range_overlap(self):
        text = "售 PS5 主機 1000-2000"
        detail = analyze_detail(text, self.rule())
        self.assertFalse(detail["matched"])
        self.assertEqual(detail["gates"][-1]["name"], "價格區間")
        self.assertFalse(detail["gates"][-1]["ok"])


if __name__ == "__main__":
    unittest.main()
