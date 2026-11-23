import unittest

from ps5hunter.generic import Rule
from ps5hunter.survey import SOLD_WORDS, aggregate_daily, build_survey_rule


class SurveyRuleTest(unittest.TestCase):
    def test_sold_words_removed_and_range_opened(self):
        rule = Rule(
            name="PS5",
            keywords=["PS5"],
            exclude=["遊戲片", "已售出", "售出"],
            require=["主機"],
            min_price=3000,
            max_price=10000,
            price_near_keyword=True,
            reject_multi_item=True,
        )
        survey = build_survey_rule(rule)
        self.assertNotIn("已售出", survey.exclude)
        self.assertIn("遊戲片", survey.exclude)
        self.assertEqual(survey.min_price, 1500)
        self.assertGreaterEqual(survey.max_price, 10**8)
        self.assertTrue(survey.price_near_keyword)
        self.assertFalse(survey.reject_multi_item)
        self.assertTrue(SOLD_WORDS)


class AggregateTest(unittest.TestCase):
    def test_daily(self):
        points = [
            {"date": "2026-11-20", "price": 5000},
            {"date": "2026-11-20", "price": 6000},
            {"date": "2026-11-22", "price": 4000},
        ]
        daily = aggregate_daily(points)
        self.assertEqual(len(daily), 2)
        self.assertEqual(daily[0]["min"], 5000)
        self.assertEqual(daily[0]["max"], 6000)
        self.assertEqual(daily[0]["avg"], 5500)
        self.assertEqual(daily[1]["count"], 1)


if __name__ == "__main__":
    unittest.main()
