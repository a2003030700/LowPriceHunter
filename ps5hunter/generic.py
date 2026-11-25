"""通用二手交易監控規則引擎 v2：
五層嚴謹判定（結案狀態、交易意圖、分區與相容性遮蔽、品項↔價格綁定、三層排除與區間檢查）。
適用 PS5 及任何其他二手商品。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .prices import PriceHit, find_prices, is_placeholder_amount

# ---------------------------------------------------------------- Layer 1: 狀態與意圖

# 合法「售出」免責／動機片語（避免誤殺「售出不退」「少玩售出」）
_SAFE_SOLD_RE = re.compile(
    r"售出不退(?:換)?|售出概不退換|售出無保(?:固)?|未售出|尚未售出|還沒售出"
    r"|(?:故|想|欲|便宜|少玩|換機|多買|閒置|搬家|退坑|出清|誠心)\s*想?售出"
)

_SOLD_RE = re.compile(
    r"已售出?|已賣出?|暫售|售完|暫時售完|已保留|保留中|已預訂|已被預訂|預訂中"
    r"|已收訂金?|已成交|已下架|已匯款|先別私訊|感謝平台|感謝社團|暫時有人收了"
    r"|缺貨|(?<![A-Za-z])sold(?![A-Za-z])|out\s+of\s+stock",
    re.IGNORECASE,
)

_AUTHOR_SOLD_RE = re.compile(
    r"已售出?|售出|售完|暫售|已保留|保留中|有人收了|已收訂|已預訂|已被預訂|先別私訊|已成交",
    re.IGNORECASE,
)

# 合法「收」白名單（避免誤殺「收納包」「收據」「收藏」「全收優先」「不收議價」）
_SAFE_SHOU_RE = re.compile(
    r"全收(?:優先)?|一起收|打包收|收納[包盒箱袋架]?|收據|收藏|收現金"
    r"|不收(?:議價|訂金|信用卡|支票)|收件|收到|回收價?|收尋|收貨|收費"
)

# 不論使用者有沒有填排除詞，一律視為「收購/徵求」的寫法
_BUYING_RE = re.compile(
    r"[\[【(（［]\s*(?:徵|收|求|徵求|誠徵|誠收|徵收|求購)\s*[\]】)）］]"
    r"|收購|誠收|徵收|想收|徵求|誠徵|求購|想買|收一台|收一組|收一隻|收一支"
    r"|(?<![不無免])預算"
    r"|(?:^|[\n\r])\s*(?:台北|新北|雙北|桃園|新竹|苗栗|台中|彰化|南投|雲林|嘉義|台南|高雄|屏東|宜蘭|花蓮|台東|基隆|全台|全省)?\s*(?:徵|收)\s*(?=[A-Za-z0-9\u4e00-\u9fff])"
)

_SWAP_ONLY_RE = re.compile(
    r"(?:^|[\n\r])\s*[\[【(（［]\s*換\s*[\]】)）］]|只換不賣|純換|求換|想換"
)
_EXPLICIT_SALE_RE = re.compile(
    r"[\[【(（［]\s*(?:售|賣|出售)\s*[\]】)）］]|售價|賣價|直購|直賣|出清|單售|拆售|(?:^|[\s\n])售\s*[:：]?\s*(?:NT)?\$?\d"
)
_INQUIRY_RE = re.compile(
    r"(?:請問|想問|問一下|請教)(?:各位)?[^\n]{0,35}(?:多少|行情|值不值|能賣|可以賣|可以玩嗎|是可以玩的嗎|怎麼賣|\?|？)"
)
_COMMERCIAL_NON_SALE_RE = re.compile(
    r"代購|日本帶回|韓國帶回|出租|租借|承租|寄修|維修服務|改機服務|抽獎|一番賞|無卡分期|零卡分期"
)
_OVERSEAS_RE = re.compile(
    r"只接受TNG|吉隆坡|馬來西亞|马来西亚|新加坡面交|香港面交|馬幣|马币|\bMYR\b|\bRM\s*\d|\bHKD\b|HK\$\s*\d|[¥￥]\s*\d",
    re.IGNORECASE,
)

# ---------------------------------------------------------------- Layer 2: 分區與遮蔽

# 相容性／適用性子句（遮蔽後不作為販售主體依據）
_COMPAT_CLAUSE_RE = re.compile(
    r"適用(?:於|型號|機型|平台)?\s*[:：]?\s*[（(]?[^。\n\r]{1,48}[）)]?"
    r"|(?:支援|可用於|用於|專用於|只用在|相容於?)\s*[^。\n\r]{1,36}"
    r"|(?:PS5|PS4|Switch|Xbox|主機)[^。\n\r]{0,16}(?:亦?可|都能|皆可|支援)(?:向下相容)?遊?玩"
    r"|向下相容[^。\n\r]{0,24}",
    re.IGNORECASE,
)

# 升級／換購動機子句（避免「升級 PS5 Pro 故出售舊 PS5 Slim」被 Pro 排除詞誤殺）
_UPGRADE_REASON_RE = re.compile(
    r"(?:因|因為|為了|想|剛|已)?(?:換購|升級|改買|買了|購入|換成|換了)\s*[^\n，,。；;]{1,18}"
)

# 雜訊欄位（交易方式、面交規則、hashtag）不作主體依據
_HASHTAG_RE = re.compile(r"#[^\s#\n]+")
_META_LINE_RE = re.compile(
    r"^\s*[【\[](?:交易方式|交易地點|交易包裝|面交地點|聯絡方式|運費|備註規則)[】\]][^\n]*$",
    re.MULTILINE,
)
_PLATFORM_LINE_RE = re.compile(
    r"^(\s*[【\[]平台[】\]]\s*[:：]?\s*)([^\n]*)$",
    re.MULTILINE,
)
_ITEM_HEADER_RE = re.compile(
    r"[【\[](?:品名|商品名稱|品名價格|品名與價格|品名/價格|商品|物品名稱|型號)[】\]]\s*[:：]?\s*([^\n]*)"
)
_PRICE_ONLY_FIELD_RE = re.compile(
    r"^\s*[【\[]?(?:商品價格|價格|售價|賣價|金額|品名價格|開價|單價)[】\]]?\s*[:：]?\s*",
    re.IGNORECASE,
)

# 附贈／配件修飾（在單一品項描述裡，用來切出主體頭詞）
_ATTACHMENT_SPLIT_RE = re.compile(
    r"(?:附贈?|贈送?|送|含|搭配|加贈|內含|配件\s*[:：]?|另外附|另附|再送)"
)
_BUNDLE_ACCESSORY_COUNT_RE = re.compile(
    r"(?:單|雙|兩|二|三|四|\d+\s*[支隻個片張組])\s*(?:原廠|副廠|全新|二手)?\s*(?:手把|控制器|搖桿|手掣|遊戲片?|光碟)"
)

_ORIGINAL_PRICE_RE = re.compile(
    r"(?:原價|原价|原售價|原售|市價|定價|建議售價|原廠價|購入價|買入價|原購入|當初買|當初購入)\s*[:：]?\s*$"
)
_SPLIT_SALE_RE = re.compile(r"拆售|拆賣|單售|單買|單主機|單機")

DEFAULT_BUNDLE_WORDS = ["合售", "整組", "全套", "打包", "一起帶", "含運"]

DEFAULT_EXCLUDES = ["已售出", "暫售", "已保留", "已成交", "sold", "已下架", "缺貨"]

RISK_WORDS = ["預付", "先匯款", "貨到付款", "代購", "急售"]


def find_risk_words(text: str) -> list[str]:
    low = (text or "").lower()
    return [word for word in RISK_WORDS if word.lower() in low]


def _mask_with_spaces(text: str, pattern: re.Pattern) -> str:
    """以等長空白遮蔽符合區段，確保字元偏移量完全不變。"""
    return pattern.sub(lambda m: " " * (m.end() - m.start()), text)


def _word_regex(word: str) -> re.Pattern:
    """純英數詞採用詞界比對；中文或混合詞維持精準子字串比對。"""
    escaped = re.escape(word)
    left = r"(?<![A-Za-z0-9])" if word and word[0].isascii() and word[0].isalnum() else ""
    right = r"(?![A-Za-z0-9])" if word and word[-1].isascii() and word[-1].isalnum() else ""
    return re.compile(left + escaped + right, re.IGNORECASE)


def _find_word_span(text: str, word: str) -> tuple[int, int] | None:
    if not word:
        return None
    m = _word_regex(word).search(text)
    return m.span() if m else None


def _contains_any(text: str, words: list[str]) -> str | None:
    for w in words:
        if w and _find_word_span(text, w) is not None:
            return w
    return None


@dataclass
class Rule:
    name: str
    keywords: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    accessory_exclude: list[str] = field(default_factory=list)
    require: list[str] = field(default_factory=list)
    ignore_numbers: list[str] = field(default_factory=list)
    min_price: int = 0
    max_price: int = 100000
    price_near_keyword: bool = False
    near_distance: int = 60
    reject_multi_item: bool = False
    bundle_words: list[str] = field(default_factory=lambda: list(DEFAULT_BUNDLE_WORDS))
    exclude_non_sale: bool = True
    require_taiwan_currency: bool = True

    @property
    def label(self) -> str:
        return self.name or "物品"


@dataclass
class Match:
    matched: bool
    keyword: str | None = None
    price: int | None = None
    price_high: int | None = None
    risk_words: list[str] | None = None
    reason: str = ""
    price_source: str = ""
    is_split_sale: bool = False


@dataclass
class _Binding:
    item_text: str
    price: PriceHit
    source: str  # "slash_pair" | "line" | "field" | "listing_card" | "proximity"
    is_split_sale: bool = False
    has_keyword: bool = False
    has_require: bool = False
    is_accessory: bool = False
    accessory_word: str | None = None
    is_target: bool = False
    in_range: bool = False
    distance: int | None = None


def rule_from_dict(raw: dict) -> Rule:
    bundle = [str(x).strip() for x in raw.get("bundle_words", []) if str(x).strip()]
    return Rule(
        name=str(raw.get("name") or "物品").strip(),
        keywords=[str(x).strip() for x in raw.get("keywords", []) if str(x).strip()],
        exclude=[str(x).strip() for x in raw.get("exclude", []) if str(x).strip()],
        accessory_exclude=[str(x).strip() for x in raw.get("accessory_exclude", []) if str(x).strip()],
        require=[str(x).strip() for x in raw.get("require", []) if str(x).strip()],
        ignore_numbers=[str(x).strip() for x in raw.get("ignore_numbers", []) if str(x).strip()],
        min_price=int(raw.get("min_price") or 0),
        max_price=int(raw.get("max_price") or 100000),
        price_near_keyword=bool(raw.get("price_near_keyword", False)),
        near_distance=int(raw.get("near_distance") or 60),
        reject_multi_item=bool(raw.get("reject_multi_item", False)),
        bundle_words=bundle or list(DEFAULT_BUNDLE_WORDS),
        exclude_non_sale=bool(raw.get("exclude_non_sale", True)),
        require_taiwan_currency=bool(raw.get("require_taiwan_currency", True)),
    )


def _min_distance(text: str, price: PriceHit, anchors: list[str]) -> int | None:
    """價格與最近產品字之間隔了幾個字。"""
    p_start = price.pos
    p_end = price.span[1] if price.span else (price.pos + len(price.raw))
    best: int | None = None
    for anchor in anchors:
        if not anchor:
            continue
        for m in _word_regex(anchor).finditer(text):
            s, e = m.span()
            if e <= p_start:
                dist = p_start - e
            elif p_end <= s:
                dist = s - p_end
            else:
                dist = 0
            if best is None or dist < best:
                best = dist
    return best


def _is_original_price(text: str, price: PriceHit) -> bool:
    if price.is_original:
        return True
    before = text[max(0, price.pos - 12): price.pos]
    return bool(_ORIGINAL_PRICE_RE.search(before))


def _is_in_range(price: PriceHit, rule: Rule) -> bool:
    return price.low <= rule.max_price and price.upper >= rule.min_price


def _extract_subject_head(seg_text: str, rule: Rule) -> str:
    """取出品項描述的主體頭詞，去掉「附贈／含／+ 配件」修飾。"""
    s = seg_text.strip()
    if not s:
        return ""
    # 先移除欄位前綴如 【品名】【售】【價格】
    s = re.sub(r"^(?:[【\[][^】\]]{1,10}[】\]]\s*[:：]?\s*)+", "", s).strip()
    # 若有 附/贈/含/搭配，取其左側為主體頭詞（前提是左側不為空）
    m_att = _ATTACHMENT_SPLIT_RE.search(s)
    if m_att and m_att.start() >= 2:
        left = s[:m_att.start()].strip()
        if left:
            s = left
    # 若有 + / ＋ 且左側已經包含關鍵字或必要詞，則右側視為附屬品
    m_plus = re.search(r"[+＋]", s)
    if m_plus and m_plus.start() >= 2:
        left = s[:m_plus.start()].strip()
        if _contains_any(left, rule.keywords) or _contains_any(left, rule.require):
            s = left
    return s


def _check_accessory_subject(seg_text: str, rule: Rule) -> tuple[bool, str | None]:
    """判斷某段品項描述的主體本身是否為配件／周邊（而非附贈品）。"""
    if not rule.accessory_exclude:
        return False, None
    head = _extract_subject_head(seg_text, rule)
    if not head:
        return False, None
    has_req = bool(rule.require and _contains_any(head, rule.require))
    # 若主體已明確含主機/必要詞，先剝除「雙手把」「2支控制器」這類數量標記再檢查
    cleaned = _BUNDLE_ACCESSORY_COUNT_RE.sub(" ", head) if has_req else head
    acc = _contains_any(cleaned, rule.accessory_exclude)
    if not acc:
        return False, None
    # 若主體完全沒有必要詞（例如「PS5 散熱器」「PS Portal 黑色」「原廠手把」），直接判定為配件
    if not has_req:
        return True, acc
    # 即使有 require 詞，若配件詞緊貼在關鍵字之後（如「PS5 外接光碟機」「PS5 搖桿外殼」），也視為配件
    for kw in rule.keywords:
        if not kw:
            continue
        adj = re.compile(
            re.escape(kw) + r"\s*(?:原廠|副廠|專用|二手|全新)?\s*" + re.escape(acc),
            re.IGNORECASE,
        )
        if adj.search(cleaned):
            return True, acc
    return False, None


def _primary_subject_text(subject_masked: str, listing_title: str) -> str:
    """抽出一篇貼文最核心的「主體品名區」（標題、品名欄或前 3 行）。"""
    parts: list[str] = []
    if listing_title:
        parts.append(listing_title)
    for m in _ITEM_HEADER_RE.finditer(subject_masked):
        val = (m.group(1) or "").strip()
        if val:
            parts.append(val)
    if not parts:
        lines = [ln.strip() for ln in subject_masked.splitlines() if ln.strip()]
        parts.extend(lines[:3])
    return "\n".join(parts)


def _strip_platform_if_redundant(subject_masked: str) -> str:
    """若【品名】/【品名價格】已有明確非純數字品名，則【平台】欄位僅屬分類雜訊，予以遮蔽。"""
    item_has_words = False
    for m in _ITEM_HEADER_RE.finditer(subject_masked):
        val = (m.group(1) or "").strip()
        # 去掉數字、幣別、斜線、空白後仍殘留中英文品名
        alpha = re.sub(r"[\d,.\s/$＄元塊萬万千kKwW\-~～至到:：]", "", val)
        if len(alpha) >= 2:
            item_has_words = True
            break
    if not item_has_words:
        return subject_masked
    return _PLATFORM_LINE_RE.sub(lambda m: " " * (m.end() - m.start()), subject_masked)


def _detect_slash_bindings(
    raw_text: str,
    subject_masked: str,
    prices: list[PriceHit],
    rule: Rule,
) -> tuple[list[_Binding], set[int]]:
    """偵測「A / B」對應「18000 / 6500」這類斜線平行對照，回傳綁定與已消耗價格位置集合。"""
    bindings: list[_Binding] = []
    consumed_pos: set[int] = set()

    lines = subject_masked.splitlines(keepends=True)
    offsets: list[tuple[int, int, str]] = []
    cur = 0
    for ln in lines:
        offsets.append((cur, cur + len(ln), ln))
        cur += len(ln)

    for idx, (s, e, ln) in enumerate(offsets):
        # 找同一行內含有 >= 2 個價格且以 / 或 、 分隔的價格行
        line_prices = [
            p for p in prices
            if s <= p.pos < e and not p.is_original and not p.is_placeholder
        ]
        if len(line_prices) < 2:
            continue
        # 檢查價格之間是否主要是斜線／頓號分隔（沒有穿插完整品名）
        line_prices.sort(key=lambda p: p.pos)
        sep_ok = True
        for i in range(len(line_prices) - 1):
            p1, p2 = line_prices[i], line_prices[i + 1]
            p1_end = p1.span[1] if p1.span else p1.pos + len(p1.raw)
            between = raw_text[p1_end: p2.pos]
            if not re.fullmatch(r"\s*[/／、|]\s*", between):
                sep_ok = False
                break
        if not sep_ok:
            continue

        # 往回最多找 2 行非空行，看有沒有對應數量的斜線品名清單
        n = len(line_prices)
        for back in range(1, 3):
            if idx - back < 0:
                break
            ps, pe, prev_ln = offsets[idx - back]
            prev_clean = re.sub(r"^\s*[【\[][^】\]]{1,10}[】\]]\s*[:：]?\s*", "", prev_ln).strip()
            if not prev_clean:
                continue
            parts = [x.strip() for x in re.split(r"[/／|]", prev_clean) if x.strip()]
            if len(parts) == n:
                for item_part, price_hit in zip(parts, line_prices):
                    bindings.append(
                        _Binding(
                            item_text=item_part,
                            price=price_hit,
                            source="slash_pair",
                            is_split_sale=bool(_SPLIT_SALE_RE.search(item_part)),
                        )
                    )
                    consumed_pos.add(price_hit.pos)
                break

    return bindings, consumed_pos


def _build_bindings(
    raw_text: str,
    subject_masked: str,
    prices: list[PriceHit],
    rule: Rule,
    listing_title: str = "",
    listing_price_hits: list[PriceHit] | None = None,
) -> list[_Binding]:
    """將所有候選價格綁定到對應的品項描述。"""
    bindings: list[_Binding] = []
    usable_prices = [p for p in prices if not _is_original_price(raw_text, p) and not p.is_placeholder]
    primary_subj = _primary_subject_text(subject_masked, listing_title)

    # 1. 斜線平行對照（最高優先，解決 PS5 / PS Portal <-> 18000 / 6500）
    slash_bindings, consumed = _detect_slash_bindings(raw_text, subject_masked, usable_prices, rule)
    bindings.extend(slash_bindings)

    # 2. 逐行與同行多價格分段綁定
    lines = subject_masked.splitlines(keepends=True)
    cur = 0
    for ln in lines:
        s, e = cur, cur + len(ln)
        cur = e
        line_prices = [p for p in usable_prices if s <= p.pos < e and p.pos not in consumed]
        if not line_prices:
            continue
        line_prices.sort(key=lambda p: p.pos)

        if len(line_prices) == 1:
            p = line_prices[0]
            p_end = p.span[1] if p.span else p.pos + len(p.raw)
            before_in_line = subject_masked[s: p.pos]
            after_in_line = subject_masked[p_end: e]
            # 檢查此行是否為「純價格欄」（如 【價格】：$16000 或 售價：11500）
            stripped_before = _PRICE_ONLY_FIELD_RE.sub("", before_in_line).strip()
            stripped_before_alpha = re.sub(r"[\s:：\$＄NTnt元塊,.-]", "", stripped_before)
            stripped_after_alpha = re.sub(
                r"(?:元|塊|含運|免運|可議|誠可議|可小刀|小刀|不議價|面交|自取|店到店|台北|新北|雙北|桃園|新竹|台中|彰化|台南|高雄|屏東|[\s,，。!！])",
                "",
                after_in_line,
            )
            if len(stripped_before_alpha) < 2 and len(stripped_after_alpha) < 2:
                # 欄位對應：繼承主體區品名
                bindings.append(
                    _Binding(
                        item_text=primary_subj,
                        price=p,
                        source="field",
                        is_split_sale=bool(_SPLIT_SALE_RE.search(before_in_line + after_in_line)),
                    )
                )
            else:
                seg = (before_in_line + " " + after_in_line).strip()
                bindings.append(
                    _Binding(
                        item_text=seg,
                        price=p,
                        source="line",
                        is_split_sale=bool(_SPLIT_SALE_RE.search(seg)),
                    )
                )
            consumed.add(p.pos)
        else:
            # 同一行有多個價格：以前一個價格結束到當前價格起始為當前品項描述
            prev_end = s
            for p in line_prices:
                seg = subject_masked[prev_end: p.pos].strip()
                p_end = p.span[1] if p.span else p.pos + len(p.raw)
                prev_end = p_end
                if not seg:
                    seg = primary_subj
                bindings.append(
                    _Binding(
                        item_text=seg,
                        price=p,
                        source="line",
                        is_split_sale=bool(_SPLIT_SALE_RE.search(seg)),
                    )
                )
                consumed.add(p.pos)

    # 3. 補充 Marketplace 卡片價格（非佔位假價）
    if listing_price_hits:
        card_subj = (listing_title + "\n" + primary_subj).strip()
        for lp in listing_price_hits:
            if lp.is_placeholder or lp.is_original:
                continue
            bindings.append(
                _Binding(
                    item_text=card_subj,
                    price=lp,
                    source="listing_card",
                    is_split_sale=False,
                )
            )

    # 4. 評估每一個綁定是否指向「目標商品」
    post_has_kw = bool(_contains_any(subject_masked, rule.keywords)) or bool(
        listing_title and _contains_any(listing_title, rule.keywords)
    )
    post_primary_is_acc, post_primary_acc_word = _check_accessory_subject(primary_subj, rule)

    anchors = rule.require or rule.keywords
    for b in bindings:
        b.in_range = _is_in_range(b.price, rule)
        b.has_keyword = bool(_contains_any(b.item_text, rule.keywords))
        b.has_require = (not rule.require) or bool(_contains_any(b.item_text, rule.require))
        is_acc, acc_w = _check_accessory_subject(b.item_text, rule)
        b.is_accessory = is_acc
        b.accessory_word = acc_w

        # 僅在結構化純價格欄、商品卡、或明確拆售主機行（如「可拆售主機+盒裝 12300元」）才繼承貼文主關鍵字，
        # 避免多品項貼文裡別的型號（如 iPhone 14 128G / PS4 主機）借用主關鍵字
        effective_kw = b.has_keyword or (
            post_has_kw
            and (b.source in ("field", "listing_card") or b.is_split_sale)
            and b.has_require
            and bool(rule.require)
            and not is_acc
        )
        # 若整篇主體本身已被判定為配件（如 WD SSD / 頸掛揚聲器）且行內不是明確主機拆售，禁止成為目標
        if b.source == "field" and post_primary_is_acc:
            b.is_accessory = True
            b.accessory_word = post_primary_acc_word

        b.is_target = bool(effective_kw and b.has_require and not b.is_accessory)
        if b.source == "listing_card":
            b.distance = 0
        elif b.source in ("slash_pair", "field"):
            # 結構化欄位／斜線對應本身即明確綁定；距離仍記錄供顯示
            d = _min_distance(subject_masked, b.price, anchors)
            b.distance = d if d is not None else 0
        else:
            b.distance = _min_distance(subject_masked, b.price, anchors)

    return bindings


def analyze_detail(
    text: str,
    rule: Rule,
    *,
    listing_title: str = "",
    listing_price: str = "",
    listing_original_price: str = "",
    listing_status: str = "",
    author_comments: list[str] | None = None,
) -> dict:
    """五層完整分析，回傳可顯示在「規則測試器」與監控引擎的詳細結果。"""
    detail = {
        "matched": False,
        "reason": "",
        "keyword": None,
        "require_hit": None,
        "excluded_word": None,
        "price": None,
        "price_high": None,
        "price_raw": None,
        "price_source": "",
        "is_split_sale": False,
        "price_is_range": False,
        "price_includes_shipping": False,
        "price_confidence": None,
        "price_count": 0,
        "price_distance": None,
        "near_limit": rule.near_distance,
        "multi_item": False,
        "spans": {},
        "gates": [],
        "price_candidates": [],
        "bindings": [],
        "risk_words": [],
    }

    def gate(name: str, ok: bool, info: str = "") -> None:
        detail["gates"].append({"name": name, "ok": bool(ok), "detail": info})

    raw_body = text or ""
    title_str = (listing_title or "").strip()
    combined = (f"{title_str}\n{raw_body}".strip()) if title_str else raw_body
    # 若沒有獨立 listing_title，offset 基準即為原始 text，確保 spans 與輸入文字完全對齊
    text_for_spans = raw_body if not title_str else combined

    if not text_for_spans.strip():
        gate("內容", False, "空白")
        detail["reason"] = "空白內容"
        return detail

    detail["risk_words"] = find_risk_words(text_for_spans)

    # ---------------- Layer 1A: 結案狀態（商品卡狀態、正文、作者留言）
    if listing_status and _SOLD_RE.search(listing_status):
        gate("結案狀態", False, f"商品卡：{listing_status.strip()}")
        detail["reason"] = f"已結案（{listing_status.strip()}）"
        return detail

    sold_check_text = _mask_with_spaces(text_for_spans, _SAFE_SOLD_RE)
    m_sold = _SOLD_RE.search(sold_check_text)
    if m_sold:
        detail["excluded_word"] = m_sold.group(0)
        detail["spans"]["exclude"] = list(m_sold.span())
        gate("結案狀態", False, m_sold.group(0))
        detail["reason"] = f"已售出／暫售／缺貨（{m_sold.group(0)}）"
        return detail

    if author_comments:
        for ac in author_comments:
            ac_clean = _mask_with_spaces(ac or "", _SAFE_SOLD_RE)
            m_ac = _AUTHOR_SOLD_RE.search(ac_clean)
            if m_ac:
                gate("結案狀態", False, f"作者留言：{m_ac.group(0)}")
                detail["reason"] = f"作者於留言更新已售出（{m_ac.group(0)}）"
                return detail
    gate("結案狀態", True, "販售中")

    # ---------------- Layer 1B: 交易意圖（徵求／收購／換物／詢問／代購）
    buy_check_text = _mask_with_spaces(text_for_spans, _SAFE_SHOU_RE)
    m_buy = _BUYING_RE.search(buy_check_text)
    if m_buy:
        detail["excluded_word"] = m_buy.group(0).strip()
        detail["spans"]["exclude"] = list(m_buy.span())
        gate("收購/徵求", False, m_buy.group(0).strip())
        detail["reason"] = "收購/徵求文"
        return detail
    gate("收購/徵求", True)

    if rule.exclude_non_sale:
        has_explicit_sale = bool(_EXPLICIT_SALE_RE.search(text_for_spans))
        if _SWAP_ONLY_RE.search(text_for_spans) and not has_explicit_sale:
            gate("交易意圖", False, "純換物／貼換")
            detail["reason"] = "純換物／貼換文"
            return detail
        if _INQUIRY_RE.search(text_for_spans) and not has_explicit_sale:
            gate("交易意圖", False, "詢問／估價")
            detail["reason"] = "詢問／估價文"
            return detail
        m_comm = _COMMERCIAL_NON_SALE_RE.search(text_for_spans)
        if m_comm:
            gate("交易意圖", False, m_comm.group(0))
            detail["reason"] = f"非一般二手出售（{m_comm.group(0)}）"
            return detail
        gate("交易意圖", True, "一般出售")

    # ---------------- Layer 1C: 全域硬排除詞（含詞界、否定與升級語境保護）
    exclude_check_base = _mask_with_spaces(text_for_spans, _UPGRADE_REASON_RE)
    for word in rule.exclude:
        if not word:
            continue
        target_str = exclude_check_base
        if word == "售出":
            target_str = _mask_with_spaces(target_str, _SAFE_SOLD_RE)
        elif word == "收":
            target_str = _mask_with_spaces(target_str, _SAFE_SHOU_RE)
        span = _find_word_span(target_str, word)
        if span is not None:
            detail["excluded_word"] = word
            detail["spans"]["exclude"] = list(span)
            gate("排除詞", False, word)
            detail["reason"] = f"含排除詞「{word}」"
            return detail
    gate("排除詞", True)

    # ---------------- Layer 2: 分區與相容性／雜訊遮蔽
    subject_masked = _mask_with_spaces(text_for_spans, _COMPAT_CLAUSE_RE)
    subject_masked = _mask_with_spaces(subject_masked, _HASHTAG_RE)
    subject_masked = _mask_with_spaces(subject_masked, _META_LINE_RE)
    subject_masked = _strip_platform_if_redundant(subject_masked)

    # 必須包含（只在有效主體／說明區生效，不含被遮蔽的相容性與雜訊區）
    if rule.require:
        req_hit = _contains_any(subject_masked, rule.require)
        if not req_hit:
            gate("必須包含", False, "缺少（已排除相容性與雜訊區）")
            detail["reason"] = "缺少必要關鍵字（可能只是平台/周邊/相容性敘述）"
            return detail
        detail["require_hit"] = req_hit
        req_span = _find_word_span(subject_masked, req_hit)
        if req_span:
            detail["spans"]["require"] = list(req_span)
        gate("必須包含", True, req_hit)
    else:
        gate("必須包含", True, "未設定")

    # 關鍵字（詞界感知）
    keyword = _contains_any(subject_masked, rule.keywords)
    if not keyword:
        gate("關鍵字", False, "缺少")
        detail["reason"] = "沒有產品關鍵字"
        return detail
    detail["keyword"] = keyword
    kw_span = _find_word_span(subject_masked, keyword)
    if kw_span:
        detail["spans"]["keyword"] = list(kw_span)
    gate("關鍵字", True, keyword)

    # ---------------- Layer 3: 價格解析與「品項 ↔ 價格」綁定
    prices = find_prices(
        text_for_spans,
        ignore_numbers=rule.ignore_numbers,
        allow_foreign=not rule.require_taiwan_currency,
    )
    listing_price_hits: list[PriceHit] = []
    if listing_price:
        lp_clean = listing_price.strip()
        if lp_clean and lp_clean != "免費":
            listing_price_hits = find_prices(
                lp_clean,
                ignore_numbers=rule.ignore_numbers,
                allow_foreign=not rule.require_taiwan_currency,
            )

    all_candidates = list(prices) + [
        lp for lp in listing_price_hits
        if not any(p.amount == lp.amount and p.upper == lp.upper for p in prices)
    ]
    detail["price_count"] = len(all_candidates)
    detail["price_candidates"] = [
        {
            "low": p.low,
            "high": p.upper,
            "raw": p.raw,
            "span": list(p.span) if p.span else None,
            "strong": p.strong,
            "confidence": p.confidence,
            "is_placeholder": p.is_placeholder,
            "is_original": p.is_original,
        }
        for p in all_candidates
    ]

    if not all_candidates:
        if rule.require_taiwan_currency and _OVERSEAS_RE.search(text_for_spans):
            gate("幣別/價格", False, "僅偵測到外幣或海外交易")
            detail["reason"] = "非台幣／海外貼文"
            return detail
        gate("價格", False, "找不到")
        detail["reason"] = "找不到價格"
        return detail

    if rule.require_taiwan_currency and _OVERSEAS_RE.search(text_for_spans) and not any(
        not p.is_foreign and not p.is_placeholder for p in all_candidates
    ):
        gate("幣別/價格", False, "非台幣標價")
        detail["reason"] = "非台幣／海外貼文"
        return detail

    non_placeholder = [p for p in all_candidates if not p.is_placeholder]
    if not non_placeholder:
        gate("價格", False, "僅佔位假價格")
        detail["reason"] = "僅佔位假價格（如 $1 / $9999 / $12345）"
        return detail
    gate("價格", True, f"{len(non_placeholder)} 個有效候選")

    # 進行「品項 ↔ 價格」綁定（先綁定，再判區間！）
    bindings = _build_bindings(
        text_for_spans,
        subject_masked,
        prices,
        rule,
        listing_title=title_str,
        listing_price_hits=listing_price_hits,
    )
    detail["bindings"] = [
        {
            "item": b.item_text[:60],
            "low": b.price.low,
            "high": b.price.upper,
            "source": b.source,
            "is_target": b.is_target,
            "is_accessory": b.is_accessory,
            "accessory_word": b.accessory_word,
            "is_split_sale": b.is_split_sale,
            "in_range": b.in_range,
        }
        for b in bindings
    ]

    target_bindings = [b for b in bindings if b.is_target]
    if not target_bindings:
        acc_hits = [b.accessory_word for b in bindings if b.is_accessory and b.accessory_word]
        if acc_hits:
            gate("品項綁定", False, f"主體為周邊／配件（{acc_hits[0]}）")
            detail["reason"] = f"主體為周邊／配件（{acc_hits[0]}）"
        else:
            gate("品項綁定", False, "所有價格皆非目標商品")
            detail["reason"] = "價格未對應到目標商品"
        return detail
    gate("品項綁定", True, f"{len(target_bindings)} 筆目標價")

    # ---------------- Layer 4: 價格區間（只用目標商品的綁定價格！）
    in_range_bindings = [b for b in target_bindings if b.in_range]
    if not in_range_bindings:
        best_target = min(target_bindings, key=lambda b: (b.price.low, b.price.upper))
        detail["price"] = best_target.price.low
        detail["price_high"] = best_target.price.upper if best_target.price.is_range else None
        if best_target.source != "listing_card" and best_target.price.span:
            detail["spans"]["price"] = list(best_target.price.span)
        gate("價格區間", False, f"目標價 {best_target.price.low:,} 不在 {rule.min_price:,}–{rule.max_price:,}")
        detail["reason"] = "價格不在設定區間"
        return detail
    gate("價格區間", True, f"{rule.min_price:,}–{rule.max_price:,}")

    # 多項商品清單檢查（當使用者主動開啟 reject_multi_item 且無合售字時）
    plausible = [
        p for p in prices
        if p.upper >= 100 and not _is_original_price(text_for_spans, p) and not p.is_placeholder
    ]
    if rule.reject_multi_item and len(plausible) >= 3 and not any(
        word.lower() in text_for_spans.lower() for word in rule.bundle_words
    ):
        detail["multi_item"] = True
        detail["price"] = max(in_range_bindings, key=lambda b: (b.price.upper, b.price.low)).price.low
        gate("多項商品", False, f"{len(plausible)} 個價格")
        detail["reason"] = f"多項商品清單（{len(plausible)} 個價格）"
        return detail
    gate("多項商品", True, "未觸發" if rule.reject_multi_item else "未啟用")

    # 挑選最佳目標綁定：優先正文/拆售/明確綁定價，再優先強訊號；若同時有整組價與拆售價都在區間內，取較低者（更划算的單主機價）
    non_card = [b for b in in_range_bindings if b.source != "listing_card"]
    pool_bindings = non_card or in_range_bindings
    strong_bindings = [b for b in pool_bindings if b.price.strong]
    pool_bindings = strong_bindings or pool_bindings

    split_bindings = [b for b in pool_bindings if b.is_split_sale]
    if split_bindings:
        chosen = min(split_bindings, key=lambda b: (b.price.low, b.price.upper))
    else:
        chosen = min(pool_bindings, key=lambda b: (b.price.low, b.price.upper))

    best = chosen.price
    detail["price"] = best.low
    detail["price_high"] = best.upper if best.is_range else None
    detail["price_raw"] = best.raw
    detail["price_source"] = chosen.source
    detail["is_split_sale"] = chosen.is_split_sale
    detail["price_is_range"] = best.is_range
    detail["price_includes_shipping"] = best.includes_shipping
    detail["price_confidence"] = best.confidence
    if chosen.source != "listing_card":
        detail["spans"]["price"] = (
            list(best.span) if best.span else [best.pos, best.pos + len(best.raw)]
        )

    # 距離檢查（結構化欄位、斜線配對、商品卡已具語意綁定，直接通過）
    distance = chosen.distance
    detail["price_distance"] = distance
    if rule.price_near_keyword:
        structured = chosen.source in ("slash_pair", "field", "listing_card")
        ok = structured or (distance is not None and distance <= rule.near_distance)
        info = (
            f"結構化綁定（{chosen.source}）"
            if structured
            else f"距 {distance} 字（上限 {rule.near_distance}）"
        )
        gate("價格靠近", ok, info)
        if not ok:
            shown = distance if distance is not None else "?"
            detail["reason"] = f"價格離產品字太遠（{shown} 字）"
            return detail
    else:
        gate("價格靠近", True, "未啟用")

    detail["matched"] = True
    detail["reason"] = "符合" + ("（拆售主機）" if chosen.is_split_sale else "")
    gate("結果", True, detail["reason"])
    return detail


def analyze_post(post, rule: Rule) -> Match:
    """分析結構化的 Post 物件（支援商品卡標題／標價／狀態與作者留言）。"""
    detail = analyze_detail(
        getattr(post, "text", "") or "",
        rule,
        listing_title=getattr(post, "listing_title", "") or "",
        listing_price=getattr(post, "listing_price", "") or "",
        listing_original_price=getattr(post, "listing_original_price", "") or "",
        listing_status=getattr(post, "listing_status", "") or "",
        author_comments=getattr(post, "author_comments", None),
    )
    return Match(
        matched=bool(detail["matched"]),
        keyword=detail["keyword"],
        price=detail["price"],
        price_high=detail.get("price_high"),
        risk_words=detail.get("risk_words") or [],
        reason=str(detail["reason"]),
        price_source=str(detail.get("price_source") or ""),
        is_split_sale=bool(detail.get("is_split_sale")),
    )


def analyze_text(text: str, rule: Rule) -> Match:
    detail = analyze_detail(text, rule)
    return Match(
        matched=bool(detail["matched"]),
        keyword=detail["keyword"],
        price=detail["price"],
        price_high=detail.get("price_high"),
        risk_words=detail.get("risk_words") or [],
        reason=str(detail["reason"]),
        price_source=str(detail.get("price_source") or ""),
        is_split_sale=bool(detail.get("is_split_sale")),
    )
