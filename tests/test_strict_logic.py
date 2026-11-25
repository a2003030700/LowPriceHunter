"""嚴謹觸發判定邏輯 v2 回歸測試：覆蓋 231 則真實 FB PS5 社團貼文案例與跨品類二手物品。"""

import unittest

from ps5hunter.generic import Rule, analyze_detail, analyze_post, rule_from_dict
from ps5hunter.prices import find_prices
from ps5hunter.scraper import Post


def make_ps5_rule(min_price: int = 5000, max_price: int = 12000) -> Rule:
    return Rule(
        name="PS5 主機（非Pro）",
        keywords=[
            "PS5", "PlayStation 5", "PlayStation5",
            "CFI-1018", "CFI-1118", "CFI-1218", "CFI-2018", "CFI-2118",
        ],
        require=[
            "主機", "光碟版", "數位版", "數位機", "光碟機版",
            "Slim", "厚機", "薄機", "單主機", "單機", "同捆",
            "初版", "CFI", "1018", "1118", "1218", "2018", "2118",
        ],
        exclude=[
            "PS5 Pro", "Pro主機",
            "故障機", "藍燈死機", "BLOD", "紅燈", "無法開機", "零件機", "殺肉",
            "空盒", "純盒子", "只有盒子", "不含主機", "不含光碟",
            "日本語專用", "日規鎖區", "日本限定", "日版鎖區", "僅限日本帳號", "CFI-2200", "2200B",
        ],
        accessory_exclude=[
            "遊戲片", "遊戲", "光碟片", "手把", "控制器", "搖桿", "手掣",
            "外殼", "保護殼", "散熱器", "散熱座", "散熱風扇", "充電座", "底座",
            "耳機", "方向盤", "擷取盒", "SSD", "硬碟", "記憶體",
            "DLC", "序號", "點數", "海報", "PS Portal", "Portal", "VR2", "PSVR", "外接光碟機",
        ],
        ignore_numbers=[
            "1000", "1018", "1100", "1118", "1200", "1218",
            "2000", "2018", "2118", "2200", "7022", "825",
        ],
        min_price=min_price,
        max_price=max_price,
        price_near_keyword=True,
        near_distance=60,
        reject_multi_item=False,
    )


class PriceV2Test(unittest.TestCase):
    def test_w_unit_and_watt_protection(self):
        hits = find_prices("售 PS5 主機 1.1w 含運")
        self.assertEqual([h.amount for h in hits], [11000])
        self.assertTrue(hits[0].includes_shipping)

        hits_w2 = find_prices("售 PS5 主機 1w2")
        self.assertEqual([h.amount for h in hits_w2], [12000])

        # 65W / 100W 充電頭不可當成 650,000 萬
        self.assertEqual(find_prices("售 GaN 65W 快充頭 500元")[0].amount, 500)
        self.assertEqual([h.amount for h in find_prices("售 65W 快充 100W 充電頭 800元")], [800])

    def test_arrow_price_drop(self):
        hits = find_prices("售 PS5 光碟版主機 15000 -> 11500")
        non_orig = [h for h in hits if not h.is_original]
        self.assertEqual([h.amount for h in non_orig], [11500])

    def test_foreign_currency_filtered(self):
        self.assertEqual(find_prices("卖PS5 有光碟 马币2400 只接受TNG"), [])
        self.assertEqual(find_prices("售 PS5 ¥200"), [])
        self.assertEqual(find_prices("售 PS5 RM 1800"), [])

    def test_non_price_numbers_and_bare_model(self):
        hits = find_prices(
            "售 PS5 2118 光碟版 系統13.2 訂金2000 店到店+60 現折500 售價11000",
            ignore_numbers=["2118", "1218", "2018"],
        )
        self.assertEqual([h.amount for h in hits], [11000])

    def test_placeholder_flagged(self):
        hits = find_prices("NT$12,345")
        self.assertTrue(hits and hits[0].is_placeholder)
        hits2 = find_prices("NT$9,999")
        self.assertTrue(hits2 and hits2[0].is_placeholder)


class RealFacebookFalsePositiveTest(unittest.TestCase):
    def setUp(self):
        self.rule = make_ps5_rule()

    def test_hist_1_game_list_with_platform_ps5(self):
        text = (
            "售 售 售 鬼武者 / 黑色行動6\n"
            "【平台】PS5\n"
            "【品名價格】\n"
            "鬼武者 $1400\n"
            "黑色行動6 $800\n"
            "【物品狀況】二手\n"
            "【交易方式】雙北面交或店到店+60"
        )
        d = analyze_detail(text, self.rule)
        self.assertFalse(d["matched"], d)

    def test_hist_2_slash_pair_ps5_and_portal(self):
        text = (
            "【售】\n"
            "【平台】PS5 光碟版 / PS Portal 黑色\n"
            "【品名價格】18000 / 6500\n"
            "【物品狀況】二手 / 全新\n"
            "【交易包裝】台南 高雄 面交"
        )
        d = analyze_detail(text, self.rule)
        self.assertFalse(d["matched"], d)
        self.assertEqual(d["price"], 18000)

    def test_hist_3_compat_and_out_of_stock(self):
        text = "【主機支援】：PS4 專用片，PS5 光碟版主機亦可向下相容遊玩（支援 60FPS 更新）"
        d = analyze_detail(
            text, self.rule,
            listing_title="【二手良品】PS4 刺客教條：奧德賽 繁體中文版 實體光碟（PS5可玩）",
            listing_price="NT$450",
            listing_status="缺貨",
        )
        self.assertFalse(d["matched"], d)

        # 即使還沒缺貨、價格在區間內，相容性語句也不能觸發
        d2 = analyze_detail(
            "售 PS4 收藏合輯 $6000，PS5 光碟版主機亦可向下相容遊玩",
            self.rule,
        )
        self.assertFalse(d2["matched"], d2)

    def test_cooler_with_cfi_models(self):
        text = "ps5散熱器 適用型號（CFI-1000、CFI-1100、CFI-1200 系列） 價格6000"
        d = analyze_detail(text, self.rule)
        self.assertFalse(d["matched"], d)

    def test_ssd_in_console_price_range(self):
        text = "【售】WD 威騰 黑標 SN850X 2TB M.2 SSD 只用在PS5主機上 $11,500 元"
        d = analyze_detail(text, self.rule)
        self.assertFalse(d["matched"], d)

    def test_game_template_with_hashtag_console(self):
        text = "售 PS5 遊戲 4 片 6000元 合售\n中壢面交 #主機只有面交"
        d = analyze_detail(text, self.rule)
        self.assertFalse(d["matched"], d)

    def test_wtb_disguised_as_marketplace_card(self):
        d = analyze_detail(
            "【收】【平台】PS5 【價格】8000~12000",
            self.rule,
            listing_title="徵收文PS5 PRO,PS5 SLIM,PS5 1218A光碟版,PS5 手把",
            listing_price="NT$12,000",
        )
        self.assertFalse(d["matched"], d)

    def test_wtb_city_prefix(self):
        d = analyze_detail(
            "預算8000～11000",
            self.rule,
            listing_title="高雄收PS5 光碟版主機",
            listing_price="NT$11,000",
        )
        self.assertFalse(d["matched"], d)

    def test_deposit_received_and_reserved(self):
        d = analyze_detail(
            "（已收訂金）先別私訊了！PS5 光碟版蜘蛛俠主機 售 11000",
            self.rule,
        )
        self.assertFalse(d["matched"], d)

    def test_author_comment_sold(self):
        post = Post(
            key="k", group_name="G", group_id="1",
            text="【售】PS5 光碟版 CFI-1218A 主機 售價 10500",
            post_id="1", url="https://facebook.com/groups/1/posts/1/",
            author_comments=["作者 暫時有人收了"],
        )
        m = analyze_post(post, self.rule)
        self.assertFalse(m.matched, m)

    def test_foreign_currency_post(self):
        d = analyze_detail("卖PS5 有光碟 主機 PS5 马币8000 只接受TNG", self.rule)
        self.assertFalse(d["matched"], d)

    def test_placeholder_card_price_only(self):
        d = analyze_detail(
            "私訊報價",
            self.rule,
            listing_title="PS5 Slim 光碟版主機",
            listing_price="NT$9,999",
        )
        self.assertFalse(d["matched"], d)

    def test_excluded_pro_faulty_jp_locked_empty_box(self):
        for text in [
            "【售】PS5 Pro 主機 光碟版 11500元",
            "【出售】PS5 光碟版 CFI-1118A（藍燈死機故障機／未拆修）售價 6000",
            "【售】PS5 Slim 日本限定機 日本語專用 CFI-2200B01 售價 11000",
            "【售】PS5 GTA6 純盒子 只有盒子+海報 5500",
            "請問各位大神 PS5 主機 現在價格能賣多少？ 10000？",
            "【換】PS5 光碟版主機 貼換 Switch 2 價值 11000",
            "售 PS5 主機 日本帶回代購 11500",
        ]:
            d = analyze_detail(text, self.rule)
            self.assertFalse(d["matched"], f"Should reject: {text} => {d}")


class RealFacebookTruePositiveTest(unittest.TestCase):
    def setUp(self):
        self.rule = make_ps5_rule()

    def test_sold_no_return_not_falsely_killed(self):
        text = "【售】PS5 Slim 光碟版主機 【商品價格】：11500 【交易方式】：台中面交，無保固售出不退"
        d = analyze_detail(text, self.rule)
        self.assertTrue(d["matched"], d)
        self.assertEqual(d["price"], 11500)

    def test_bundle_with_games_controllers_storage_bag_not_killed(self):
        text = (
            "【售】PS5 光碟版 CFI-1218A 主機\n"
            "【價格】：$11,000\n"
            "【配件】：原廠控制器*2、附收納包、附發票收據、贈送2片遊戲片、全收優先、不收議價"
        )
        d = analyze_detail(text, self.rule)
        self.assertTrue(d["matched"], d)
        self.assertEqual(d["price"], 11000)

    def test_marketplace_title_only_has_ps5_and_price(self):
        post = Post(
            key="k2", group_name="G", group_id="1",
            text="小孩不玩想賣彰化面交",
            post_id="2", url="https://facebook.com/groups/1/posts/2/",
            listing_title="PS5厚機",
            listing_price="NT$11,000",
        )
        m = analyze_post(post, self.rule)
        self.assertTrue(m.matched, m)
        self.assertEqual(m.price, 11000)

    def test_split_sale_console_matched_while_bundle_above_max(self):
        text = (
            "PS5 Slim 2018B 數位版 1TB 系統13.2 雙手把 售14800元 "
            "可拆售主機+盒裝+原廠HDMI線+原廠電源線11800元 "
            "原廠手把+副廠Type-c線1300元"
        )
        d = analyze_detail(text, self.rule)
        self.assertTrue(d["matched"], d)
        self.assertEqual(d["price"], 11800)
        self.assertTrue(d["is_split_sale"])

    def test_upgrade_to_pro_selling_old_slim_not_killed(self):
        text = "因升級 PS5 Pro 故便宜售出舊的 PS5 Slim 光碟版主機 11000元 台北面交"
        d = analyze_detail(text, self.rule)
        self.assertTrue(d["matched"], d)
        self.assertEqual(d["price"], 11000)

    def test_main_console_plus_separate_games_picks_console_price(self):
        # 主機 11500、遊戲 1000 / 1200，應精準綁定主機 11500
        text = (
            "【售】 PS5 光碟版CFI-1218A\n"
            "【價格】：$11500\n"
            "忍者外傳4 $1000\n"
            "黎明行者之怒 鐵盒版 1200\n"
            "全收優先 可小刀"
        )
        d = analyze_detail(text, self.rule)
        self.assertTrue(d["matched"], d)
        self.assertEqual(d["price"], 11500)

    def test_arrow_drop_into_budget(self):
        text = "【售】PS5 光碟版主機 15000 -> 11200 雙北面交"
        d = analyze_detail(text, self.rule)
        self.assertTrue(d["matched"], d)
        self.assertEqual(d["price"], 11200)


class CrossCategoryUniversalTest(unittest.TestCase):
    def test_iphone_15_excludes_pro_and_accessories(self):
        rule = rule_from_dict({
            "name": "iPhone 15",
            "keywords": ["iPhone 15"],
            "require": ["128G", "256G", "512G", "空機", "手機"],
            "exclude": ["15 Pro", "Pro Max", "Plus", "鎖機", "空盒"],
            "accessory_exclude": ["保護殼", "手機殼", "保護貼", "充電線"],
            "ignore_numbers": ["128", "256", "512"],
            "min_price": 10000,
            "max_price": 18000,
            "price_near_keyword": True,
            "near_distance": 60,
        })
        # 合法單機＋附保護殼不誤殺
        ok = analyze_detail("售 iPhone 15 256G 空機 16500 附保護殼與保護貼 售出不退", rule)
        self.assertTrue(ok["matched"], ok)
        self.assertEqual(ok["price"], 16500)

        # 賣保護殼寫適用 iPhone 15 256G 不誤報
        bad1 = analyze_detail("售犀牛盾保護殼 適用 iPhone 15 256G 12000元大批出清", rule)
        self.assertFalse(bad1["matched"], bad1)

        # 賣 iPhone 15 Pro 排除
        bad2 = analyze_detail("售 iPhone 15 Pro 256G 17500", rule)
        self.assertFalse(bad2["matched"], bad2)

        # 斜線並列 iPhone 15 / AirPods -> 20000 / 12000 不應誤抓 12000
        bad3 = analyze_detail("【品名】iPhone 15 256G / AirPods Max\n【價格】20000 / 12000", rule)
        self.assertFalse(bad3["matched"], bad3)
        self.assertEqual(bad3["price"], 20000)


if __name__ == "__main__":
    unittest.main()
