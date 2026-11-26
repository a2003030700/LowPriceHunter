import unittest

from ps5hunter.dedup import (
    clean_fb_text,
    content_fingerprint,
    extract_core_body,
    find_cross_group_duplicate,
    normalize_author,
    normalize_for_dedup,
    text_similarity,
)


class FbObfuscationCleanTest(unittest.TestCase):
    def test_strips_real_world_fb_decoy_samples(self):
        s1 = (
            "Henry ChennotosSpdrel3tm8846886g2i8u5lcgf1ch55剛h054l5u3h1muu16g028a剛m1 "
            "分享對象：公開社團售全新PS5 SILM 數位版（日語版）10000/SWITCH2（日語版）"
            "10000ZNyP9Qvw.comHenry Chen 貼文的相片 "
            "9gW6R4Gkwgs3ZTWvcz12NaidyGs4UEm3NIEnotosSpdrerrtm88468a6gei8u5lcgf1chn5oh054lMu3hLmuu16g028e m1"
        )
        s2 = (
            "Henry Chen 分享對象：公開社團售全新PS5 SILM 數位版（日語版）"
            "10000/SWITCH2（日語版）10000GddqTZe2J.comHenry Chen 貼文的相片 "
            "ek5vJNM5SdwEhxi8c7QoPsaMlfcVXmE72xdOrGSv5sptyhDabnpjzuro6nZdy3QWK50xBNYCj7J3"
        )
        s3 = (
            "Henry Chen 分享對象：公開社團售全新PS5 SILM 數位版（日語版）"
            "10000/SWITCH2（日語版）10000KREWwrsrES.comHenry Chen 貼文的相片 "
            "pTZ5I7EZDR77Vms8T0Wk1HEa1uxklgtkrLBLnNU2La3"
        )

        for raw in (s1, s2, s3):
            cleaned = clean_fb_text(raw)
            self.assertIn("售全新PS5 SILM 數位版（日語版）10000/SWITCH2（日語版）10000", cleaned)
            self.assertNotIn("notosSpdr", cleaned)
            self.assertNotIn(".com", cleaned)
            self.assertNotIn("ek5vJNM", cleaned)
            self.assertNotIn("9gW6R4", cleaned)
            self.assertNotIn("pTZ5I7", cleaned)

        # 三次不同亂碼抓下來的貼文，指紋必須完全一致
        fp1 = content_fingerprint("Henry Chen", s1)
        fp2 = content_fingerprint("Henry Chen", s2)
        fp3 = content_fingerprint("Henry Chen", s3)
        self.assertIsNotNone(fp1)
        self.assertEqual(fp1, fp2)
        self.assertEqual(fp2, fp3)

    def test_preserves_normal_models_and_real_domains(self):
        text = "售 PS5 Slim CFI-2018A01 數位版 9800 元，可看 shopee.com 或 facebook.com 照片"
        cleaned = clean_fb_text(text)
        self.assertIn("CFI-2018A01", cleaned)
        self.assertIn("shopee.com", cleaned)
        self.assertIn("facebook.com", cleaned)
        self.assertIn("9800", cleaned)

    def test_cross_public_and_private_group_headers_match(self):
        pub = "王小明 分享對象：公開社團\n【售】PS5 光碟版 附雙手把 台北面交 9500"
        priv = "王小明 · 追蹤 3分鐘前 分享對象：私密社團\n[售] PS5 光碟版 附雙手把 台北面交 9500"
        self.assertEqual(
            normalize_for_dedup(pub, author="王小明"),
            normalize_for_dedup(priv, author="王小明"),
        )
        self.assertEqual(
            content_fingerprint("王小明", pub),
            content_fingerprint("王小明 精選貢獻者", priv),
        )


class CrossGroupSellerDedupTest(unittest.TestCase):
    def setUp(self):
        self.recent = [
            {
                "key": "g1_1001",
                "group": "PS5.PS4.Switch 買賣交流區",
                "url": "https://www.facebook.com/groups/1/posts/1001/",
                "price": 10000,
                "text": "Henry Chen 分享對象：公開社團 售全新PS5 SLIM 數位版 日語版 10000 台北面交",
                "found_at": "2026-11-26T05:10:00+00:00",
                "rule": "PS5",
                "author": "Henry Chen",
            }
        ]

    def test_layer1_exact_normalized_cross_group(self):
        dup = find_cross_group_duplicate(
            author="",
            rule_name="PS5",
            price=10000,
            text="分享對象：私密社團 售全新PS5 SLIM 數位版 日語版 10000 台北面交",
            listing_title="",
            recent_deals=self.recent,
            current_group="台灣二手電玩交易社",
        )
        self.assertIsNotNone(dup)
        self.assertEqual(dup["layer"], 1)
        self.assertTrue(dup["cross_group"])

    def test_layer2_same_seller_same_price_different_wording_in_other_group(self):
        # 賣家在另一個社團完全改寫文案，但同賣家＋同規則＋同價位 -> 應擋掉
        dup = find_cross_group_duplicate(
            author="Henry Chen",
            rule_name="PS5",
            price=10000,
            text="全新未拆主機便宜出清 PS5 意者私訊 10000",
            listing_title="",
            recent_deals=self.recent,
            current_group="台北電玩面交社",
        )
        self.assertIsNotNone(dup)
        self.assertEqual(dup["layer"], 2)
        self.assertIn("Henry Chen", dup["reason"])

    def test_layer2_allows_price_drop(self):
        # 同賣家降價重發（10000 -> 9000），必須照常放行通知！
        dup = find_cross_group_duplicate(
            author="Henry Chen",
            rule_name="PS5",
            price=9000,
            text="降價急售！全新PS5 SLIM 數位版 日語版 9000 台北面交",
            listing_title="",
            recent_deals=self.recent,
            current_group="台北電玩面交社",
        )
        self.assertIsNone(dup)

    def test_layer2_allows_much_pricier_different_model(self):
        # 同賣家另賣高階款（例如 PS5 Pro 17500），高出 > 5% 且文案不同 -> 應放行
        dup = find_cross_group_duplicate(
            author="Henry Chen",
            rule_name="PS5",
            price=17500,
            text="另售二手 PS5 Pro 2TB 含直立架與原盒裝 新竹可測機 17500",
            listing_title="",
            recent_deals=self.recent,
            current_group="台北電玩面交社",
        )
        self.assertIsNone(dup)

    def test_layer2_allows_different_rule(self):
        # 同賣家同價格但不同規則（Switch 2）-> 不該被 PS5 擋掉
        dup = find_cross_group_duplicate(
            author="Henry Chen",
            rule_name="Switch 2",
            price=10000,
            text="售全新 Nintendo Switch 2 主機 10000 元",
            listing_title="",
            recent_deals=self.recent,
            current_group="Switch 買賣社",
        )
        self.assertIsNone(dup)

    def test_layer3_fuzzy_similarity_minor_edits(self):
        # 沒抓到賣家名，且多加了 hashtag 與些許字眼 -> Layer 3 相似度應擋掉
        dup = find_cross_group_duplicate(
            author="",
            rule_name="PS5",
            price=10000,
            text="售全新PS5 SLIM 數位版 日語版 10000 台北面交 #PS5 #出清",
            listing_title="",
            recent_deals=self.recent,
            current_group="台灣二手電玩交易社",
        )
        self.assertIsNotNone(dup)
        self.assertEqual(dup["layer"], 3)

    def test_different_seller_different_post_allowed(self):
        dup = find_cross_group_duplicate(
            author="林大華",
            rule_name="PS5",
            price=10000,
            text="出清自用 PS5 光碟版 CFI-1218A 附戰神光碟與兩支手把 高雄面交 10000",
            listing_title="",
            recent_deals=self.recent,
            current_group="高雄電玩社",
        )
        self.assertIsNone(dup)

    def test_similarity_and_author_helpers(self):
        self.assertEqual(normalize_author("Henry Chen 精選貢獻者"), "henrychen")
        self.assertGreater(
            text_similarity(
                normalize_for_dedup("售全新PS5 SLIM 數位版 日語版 10000 台北面交"),
                normalize_for_dedup("售全新PS5 SLIM 數位版 日語版 10000 台北面交 可議"),
            ),
            0.78,
        )
        body = extract_core_body("Henry Chen 分享對象：公開社團 售全新PS5 10000", author="Henry Chen")
        self.assertEqual(body, "售全新PS5 10000")


if __name__ == "__main__":
    unittest.main()
