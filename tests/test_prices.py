import unittest

from ps5hunter.generic import Rule, analyze_detail
from ps5hunter.prices import find_prices


class PriceFormatTest(unittest.TestCase):
    def single(self, text):
        hits = find_prices(text)
        self.assertTrue(hits, text)
        return hits[0]

    def test_currency(self):
        self.assertEqual(self.single("NT$5,000").amount, 5000)
        self.assertEqual(self.single("$5000").amount, 5000)

    def test_suffix(self):
        self.assertEqual(self.single("5000元").amount, 5000)
        self.assertEqual(self.single("5000 元").amount, 5000)

    def test_context(self):
        self.assertEqual(self.single("售5000").amount, 5000)
        self.assertEqual(self.single("面交5000").amount, 5000)

    def test_wan_k(self):
        self.assertEqual(self.single("1萬2").amount, 12000)
        self.assertEqual(self.single("12k").amount, 12000)
        self.assertEqual(self.single("1.2萬").amount, 12000)

    def test_range(self):
        hit = self.single("5000-6000")
        self.assertEqual((hit.amount, hit.high), (5000, 6000))
        self.assertTrue(hit.is_range)

    def test_range_tilde(self):
        hit = self.single("5000~6000")
        self.assertEqual((hit.amount, hit.high), (5000, 6000))

    def test_shipping(self):
        self.assertTrue(self.single("5000含運").includes_shipping)
        self.assertTrue(self.single("12000含運費").includes_shipping)

    def test_negotiable_only(self):
        self.assertEqual(find_prices("可議"), [])
        self.assertEqual(find_prices("誠可議"), [])

    def test_negotiable_keeps_number(self):
        # 買家通常不討價還價：可議／誠可議 不影響價格，仍用前面的數字
        self.assertEqual(self.single("5000可議").amount, 5000)
        self.assertEqual(self.single("5000 誠可議").amount, 5000)
        self.assertEqual(self.single("售 5000 誠可議").amount, 5000)

    def test_year_and_capacity_not_price(self):
        self.assertEqual(find_prices("保固到2026年"), [])
        self.assertEqual(find_prices("128G"), [])
        self.assertEqual(find_prices("CFI-1218"), [])

    def test_qian_with_tail_digits(self):
        self.assertEqual(self.single("售 PS5 9千500").amount, 9500)

    def test_quantities_and_model_are_not_prices(self):
        hits = find_prices("售 1 台 PS5 CFI-1218 附 2 支手把 9000元")
        self.assertEqual([h.amount for h in hits], [9000])

    def test_year_without_unit(self):
        hits = find_prices("售 2024 購入 PS5 主機 9500")
        self.assertEqual([h.amount for h in hits], [9500])


class RangeRuleTest(unittest.TestCase):
    def rule(self, min_price=3000, max_price=10000):
        return Rule(
            name="PS5",
            keywords=["PS5"],
            require=["主機"],
            min_price=min_price,
            max_price=max_price,
            reject_multi_item=False,
        )

    def test_range_overlap(self):
        detail = analyze_detail("售 PS5 主機 5000-6000", self.rule())
        self.assertTrue(detail["matched"])
        self.assertEqual(detail["price"], 5000)
        self.assertEqual(detail["price_high"], 6000)
        self.assertTrue(detail["price_is_range"])

    def test_inclusive_lower_boundary(self):
        detail = analyze_detail("售 PS5 主機 5000", self.rule(5000, 6000))
        self.assertTrue(detail["matched"])

    def test_inclusive_upper_boundary(self):
        detail = analyze_detail("售 PS5 主機 6000", self.rule(5000, 6000))
        self.assertTrue(detail["matched"])

    def test_overlap_with_one_endpoint(self):
        self.assertTrue(analyze_detail("售 PS5 主機 5000-6000", self.rule(5000, 5500))["matched"])
        self.assertTrue(analyze_detail("售 PS5 主機 5000-6000", self.rule(5500, 6000))["matched"])

    def test_no_overlap(self):
        detail = analyze_detail("售 PS5 主機 5000-6000", self.rule(7000, 8000))
        self.assertFalse(detail["matched"])

    def test_single_price_still_works(self):
        detail = analyze_detail("售 PS5 主機 8500", self.rule())
        self.assertTrue(detail["matched"])
        self.assertEqual(detail["price"], 8500)
        self.assertIsNone(detail["price_high"])

    def test_original_price_not_chosen(self):
        detail = analyze_detail(
            "售 PS5 主機 原價 17580 降售 9500",
            self.rule(0, 20000),
        )
        self.assertTrue(detail["matched"])
        self.assertEqual(detail["price"], 9500)

    def test_multi_item_counts_prices_outside_range(self):
        rule = Rule(
            name="PS5",
            keywords=["PS5"],
            require=["主機"],
            min_price=5000,
            max_price=6000,
            reject_multi_item=True,
        )
        detail = analyze_detail(
            "售 PS5 主機 5000 售 800 售 900 售 1000 售 1100",
            rule,
        )
        self.assertTrue(detail["multi_item"])
        self.assertFalse(detail["matched"])


if __name__ == "__main__":
    unittest.main()
