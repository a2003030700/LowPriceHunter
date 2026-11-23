"""貼文內容分析：判斷是否為 PS5 出售文，並抓出價格。"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------- PS5 關鍵字

_PS5_RE = re.compile(
    r"(?<![a-z0-9])ps\s*5(?![0-9])"          # PS5 / PS 5
    r"|playstation\s*5"
    r"|playstation5",
    re.IGNORECASE,
)

# 這些字樣代表「不是出售」（徵求、已售出…）
_EXCLUDE_RE = re.compile(
    r"徵求|誠徵|求購|收購|想收|想買|拜託|已售|已賣|售完|已保留|已預訂|(?<!未)售出"
    r"|暫售|已下架|已成交|已匯款"
    r"|主機(?:可以|可|也能|都能|皆可)玩"
    r"|[\[【(（]\s*收\s*[\]】)）]"                    # 【收】、(收)
    r"|(?:^|[\s：:，,。])收(?:一台|一個|個|購|ps|PS|Ps|主機|Switch|switch)"
    r"|誠收|徵收|預算"                                  # 預算＝收購文的強訊號
    r"|(?:^|[\s\[【(（,，、])徵",  # 開頭的「徵」＝徵求
)

# ---------------------------------------------------------------- 價格解析

_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
_UNIT = r"(?:萬|万|千|k|K)?"
_CUR = r"(?:NT\s*\$|NTD|TWD|台幣|新台幣|\$|＄)"

# 強訊號：有貨幣符號、有「元/塊」，或數字後面直接接「萬/千」
_STRONG_PATTERNS = [
    re.compile(_CUR + r"\s*(" + _NUM + r")\s*" + _UNIT),
    re.compile(r"(" + _NUM + r")\s*(?:元|塊|圓|台幣|TWD|NTD|NT\$)"),
    re.compile(r"(" + _NUM + r")\s*(萬|万|千|k|K)(?![0-9A-Za-z])"),
]

# 弱訊號：純數字，但前後文要出現賣價相關字
_WEAK_NUM_RE = re.compile(r"(?<![\d.,])(" + _NUM + r")(?![\d])")

# 數字後面接這些單位時，不是價格（年份、尺寸、容量、成新…）
_NOT_PRICE_SUFFIX_RE = re.compile(
    r"^\s*(?:[A-Za-z]|年|月|日|號|吋|寸|GB|TB|Gb|Tb|g|G|代|版|成|樓|人|個|組|顆|片|次|%|％|～|~|-|—|/|:|：)"
)

_PRICE_CONTEXT_RE = re.compile(r"售|賣|降|出清|出價|預算|可議|含運|自取|運費|開價|折|便宜|收購|價格")

# 必須像「主機」而不是遊戲片／手把／周邊
_CONSOLE_RE = re.compile(
    r"主機|光碟版|數位版|數位機|slim|pro\b|1218|1200\s*型|1000\s*型|戰神|同捆|單主機|整台|CFI-?\s*\d|console",
    re.IGNORECASE,
)
_ACCESSORY_RE = re.compile(
    r"遊戲片|遊戲|手把|控制器|搖桿|手掣|耳機|充電|底座|散熱|收納|外殼|保護貼|周邊|序號|點數"
)

# 合理價格範圍（PS5 周邊也有幾百元的，所以下限放寬）
_MIN_PRICE = 300
_MAX_PRICE = 120_000
_MIN_CONSOLE_PRICE = 3_000  # 低於此金額幾乎都是遊戲片或手把


def _to_amount(num: str, unit: str | None) -> int | None:
    raw = num.replace(",", "")
    if raw.count(".") > 1:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if unit in ("萬", "万"):
        value *= 10_000
    elif unit in ("千", "k", "K"):
        value *= 1_000
    if value <= 0:
        return None
    return int(round(value))


@dataclass(frozen=True)
class PriceHit:
    amount: int
    raw: str
    strong: bool
    pos: int


def find_prices(text: str) -> list[PriceHit]:
    """回傳貼文中所有看起來像價格的數字（去重、依金額排序）。"""
    hits: list[PriceHit] = []
    occupied: list[tuple[int, int]] = []

    for pattern in _STRONG_PATTERNS:
        for m in pattern.finditer(text):
            span = m.span()
            if any(s <= span[0] < e for s, e in occupied):
                continue
            num = m.group(1)
            unit = m.group(2) if m.lastindex and m.lastindex >= 2 else None
            amount = _to_amount(num, unit)
            if amount is None or not (_MIN_PRICE <= amount <= _MAX_PRICE):
                continue
            hits.append(PriceHit(amount, m.group(0).strip(), True, span[0]))
            occupied.append(span)

    for m in _WEAK_NUM_RE.finditer(text):
        span = m.span()
        if any(s <= span[0] < e for s, e in occupied):
            continue
        if _NOT_PRICE_SUFFIX_RE.match(text[m.end():]):
            continue
        amount = _to_amount(m.group(1), None)
        if amount is None or not (_MIN_PRICE <= amount <= _MAX_PRICE):
            continue
        before = text[max(0, span[0] - 20): span[0]]
        after = text[span[1]: span[1] + 8]
        if not _PRICE_CONTEXT_RE.search(before) and not _PRICE_CONTEXT_RE.search(after):
            continue
        hits.append(PriceHit(amount, m.group(0).strip(), False, span[0]))
        occupied.append(span)

    hits.sort(key=lambda h: (h.amount, h.pos))
    # 去重同樣金額
    seen: set[int] = set()
    unique: list[PriceHit] = []
    for h in hits:
        if h.amount in seen:
            continue
        seen.add(h.amount)
        unique.append(h)
    return unique


def _select_price(text: str, prices: list[PriceHit]) -> PriceHit:
    """從多個價格中挑出「主機」的價格。

    優先採用附近有「售/賣/價/降」等字樣的價格，並在合理範圍內取最高者，
    避免把遊戲片（$800）或手把（$1800）當成主機價。
    """
    contextual = []
    for hit in prices:
        before = text[max(0, hit.pos - 18): hit.pos]
        if hit.strong or _PRICE_CONTEXT_RE.search(before):
            contextual.append(hit)
    pool = contextual or prices
    big = [p for p in pool if p.amount >= 3000]
    if big:
        return max(big, key=lambda p: p.amount)
    return min(pool, key=lambda p: p.amount)


@dataclass
class Analysis:
    is_ps5: bool
    excluded: bool
    price: int | None
    price_raw: str | None
    matched_keyword: str | None
    reason: str
    under_threshold: bool = True

    @property
    def is_deal(self) -> bool:
        return (
            self.is_ps5
            and not self.excluded
            and self.price is not None
            and self.under_threshold
        )


def analyze(text: str, threshold: int) -> Analysis:
    if not text:
        return Analysis(False, False, None, None, None, "空白內容")

    kw = _PS5_RE.search(text)
    if not kw:
        return Analysis(False, False, None, None, None, "沒有 PS5 關鍵字")

    if _EXCLUDE_RE.search(text):
        return Analysis(True, True, None, None, kw.group(0), "徵求/已售出文，略過")

    prices = find_prices(text)
    if not prices:
        return Analysis(True, False, None, None, kw.group(0), "找不到價格")

    best = _select_price(text, prices)
    if best.amount < _MIN_CONSOLE_PRICE:
        return Analysis(
            True, False, best.amount, best.raw, kw.group(0),
            "價格低於主機合理範圍（可能是遊戲片/手把）", under_threshold=False,
        )

    # 「PS5主機可以玩」這種相容性說明不算賣主機：主機字樣必須靠近價格
    near_console = any(abs(m.start() - best.pos) <= 30 for m in _CONSOLE_RE.finditer(text))
    if not near_console and _ACCESSORY_RE.search(text):
        return Analysis(
            True, True, best.amount, best.raw, kw.group(0),
            "主機字樣離價格太遠，且含遊戲/周邊字眼",
        )
    if not near_console and len(prices) >= 3:
        return Analysis(
            True, True, best.amount, best.raw, kw.group(0),
            "多項商品清單，疑似遊戲片",
        )
    if best.amount > threshold:
        return Analysis(
            True, False, best.amount, best.raw, kw.group(0),
            f"價格 {best.amount} 高於門檻 {threshold}", under_threshold=False,
        )
    return Analysis(True, False, best.amount, best.raw, kw.group(0), "符合條件")
