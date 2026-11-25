"""價格解析 v2：支援貨幣、元/塊、萬/千/k/w、區間價、箭頭降價、外幣與佔位假價偵測。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

_AMOUNT = r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
_UNIT = r"(?:萬|万|千|[kKwW])"
_CUR = r"(?:NT\s*\$|NTD|TWD|台幣|新台幣|\$|＄)"

_SINGLE_RE = re.compile(
    r"(?:(?P<cur>" + _CUR + r")\s*)?"
    r"(?P<num>" + _AMOUNT + r")"
    r"(?:\s*(?P<unit>" + _UNIT + r")(?P<sub>\d{1,4})?)?"
    r"(?:\s*(?P<suffix>元|塊|圓|台幣|TWD|NTD|NT\$))?",
    re.IGNORECASE,
)

# 區間符號（注意 - 不能接 >，否則會吃到箭頭降價 ->）
_RANGE_SEP_RE = re.compile(r"\s*(?:-(?!>)|–|—|~|～|至|到)\s*")
# 箭頭或降價符號：15000 -> 11500、15000→11500、15000 降售 11500
_ARROW_DROP_RE = re.compile(r"\s*(?:->|-->|→|➡|⇒|降為|降至|降價為|降售|改售|特價)\s*")

# 緊貼英文字母（無空白，如 128G / 2018B / 1218A）或明確非貨幣單位（如 256 GB / 60 FPS / 2026年）
# 注意：絕不可用 ^\s*[A-Za-z]，否則會把 "$15000 iPhone" / "11000 PS5" / "9500 Slim" 全部誤殺！
_HARD_SUFFIX_BLOCK_RE = re.compile(
    r"^(?:[A-Za-z]"
    r"|\s*(?:年|月|日|號|型|吋|寸|瓦|公分|代|版|成|%|％|:|：|期|個月|小時|分鐘|年保)"
    r"|\s*(?:GB|TB|MB|KB|Gb|Tb|Hz|FPS|fps|cm|mm|kg|hr)(?![A-Za-z]))"
)
# 量詞後綴：排除 台北/台中/台南/台東/台灣、雙北/雙手把，且僅在無幣別/無元塊且數值 < 1000 時視為數量
_QUANTIFIER_SUFFIX_RE = re.compile(
    r"^\s*(?:台(?![北中南東灣湾])|雙(?![北手])|樓|人|個|組|顆|片|次"
    r"|支|張|套|件|隻|條|瓶|盒|包|罐|捲|對|杯|本|份|間|棟|輛|部|艘|架|款)"
)
_SLASH_DATE_RE = re.compile(r"^/\d{1,2}(?!\d)")

_MODEL_PREFIX_RE = re.compile(
    r"(?:CFI|CUH|ILCE|RTX|GTX|RX|SN|型號|型号|MODEL|系統|系统|版本|序號|序号|尾數|尾数|編號|编号)\s*[-:：]?\s*$",
    re.IGNORECASE,
)
# 品牌／產品代號後面接小數字（無貨幣符號時，如 iPhone 15、Switch 2、PS 5、Pixel 9）不是價格
_BRAND_MODEL_PREFIX_RE = re.compile(
    r"(?:iPhone|iPad|MacBook|Switch|PlayStation|PS|Pixel|Galaxy|Watch|Series|Gen|Mark|Mk|Pro|Air|Ultra|Plus|Note|Fold|Flip|Tab|S|M)\s*[-]?$",
    re.IGNORECASE,
)
_NON_PRICE_PREFIX_RE = re.compile(
    r"(?:訂金|定金|押金|月付|每期|頭期|头期|運費|运费|店到店\s*\+|宅配\s*\+|超商\s*\+|郵寄\s*\+|寄送\s*\+|加購|加购"
    r"|現折|再折|折抵|立折|減價|保固至|保固到|到期|滿額折|現省)\s*[:：+]?\s*$",
    re.IGNORECASE,
)
_WATT_PREFIX_RE = re.compile(r"(?:電源|充電|快充|瓦數|功率|輸出|PD|GaN|變壓器)\s*$", re.IGNORECASE)
_YEAR_PREFIX_RE = re.compile(r"(?:於|在|西元|保固到|保固至|購買於|購入於|出廠於?)\s*$")
_YEAR_CONTEXT_RE = re.compile(r"^\s*(?:年|購入|購買|買入|購自|出廠|製造|公司貨|保固)")
_DATE_RE = re.compile(r"^(?:19|20)\d{2}\s*[-/.]\s*\d{1,2}")

_FOREIGN_CUR_BEFORE_RE = re.compile(
    r"(?:馬幣|马币|RM|MYR|港幣|港币|HKD|HK\$|日幣|日圓|日元|JPY|[¥￥]"
    r"|人民幣|人民币|RMB|CNY|美金|美元|USD|US\$|新幣|SGD|S\$)\s*$",
    re.IGNORECASE,
)
_FOREIGN_CUR_AFTER_RE = re.compile(
    r"^\s*(?:馬幣|马币|RM|MYR|港幣|港币|HKD|日幣|日圓|日元|JPY|人民幣|人民币|RMB|CNY|美金|美元|USD|SGD)(?![A-Za-z])",
    re.IGNORECASE,
)

_ORIGINAL_PRICE_RE = re.compile(
    r"(?:原價|原价|原售價|原售|市價|定價|建議售價|原廠價|購入價|買入價|原購入|當初買|當初購入)\s*[:：]?\s*$"
)

_CONTEXT_RE = re.compile(
    r"售|賣|卖|價|价|降|出清|出價|面交|自取|含運|運費|可議|開價|折|便宜|收購|預算|費用|元|塊"
    r"|直購|拆售|合售|整組|全套|割愛|釋出|轉讓|甜甜價|單主機|單機|單買|打包"
)
_AFTER_LOC_CONTEXT_RE = re.compile(
    r"^\s*(?:台北|新北|雙北|桃園|新竹|苗栗|台中|彰化|南投|雲林|嘉義|台南|高雄|屏東|宜蘭|花蓮|台東|基隆)"
)
_SHIP_RE = re.compile(r"含運|含運費|運費內含|免運")

# 常見買賣社團／Marketplace 佔位假價格
_PLACEHOLDER_AMOUNTS = frozenset({
    0, 1, 10, 99, 111, 123, 666, 999,
    1111, 1234, 6666, 9999,
    11111, 12345, 66666, 99999,
    111111, 123456, 666666, 999999,
})


@dataclass(frozen=True)
class PriceHit:
    amount: int
    raw: str
    strong: bool
    pos: int
    span: tuple[int, int] | None = None
    high: int | None = None
    currency: str = "TWD"
    is_range: bool = False
    includes_shipping: bool = False
    confidence: float = 1.0
    is_placeholder: bool = False
    is_original: bool = False
    is_foreign: bool = False

    @property
    def low(self) -> int:
        return self.amount

    @property
    def upper(self) -> int:
        return self.high if self.high is not None else self.amount


def is_placeholder_amount(amount: int) -> bool:
    return amount in _PLACEHOLDER_AMOUNTS


def _convert(num: str, unit: str | None, sub: str | None, before: str = "") -> int | None:
    raw = num.replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    if unit in ("萬", "万", "w", "W"):
        # 避免把 65W / 100W / 850W 功率瓦數誤判成「萬」
        if unit in ("w", "W") and (value >= 50 or _WATT_PREFIX_RE.search(before)):
            return None
        value *= 10_000
        if sub:
            sub_value = int(sub)
            if len(sub) == 1:
                value += sub_value * 1_000
            elif len(sub) == 2:
                value += sub_value * 100
            elif len(sub) == 3:
                value += sub_value * 10
            else:
                value += sub_value
    elif unit in ("千", "k", "K"):
        value *= 1_000
        if sub:
            sub_value = int(sub)
            if len(sub) == 1:
                value += sub_value * 100
            elif len(sub) == 2:
                value += sub_value * 10
            elif len(sub) == 3:
                value += sub_value
    if value <= 0:
        return None
    return int(round(value))


def _is_foreign_at(text: str, start: int, end: int, cur: str | None) -> bool:
    if cur and re.search(r"NT|TWD|台幣", cur, re.IGNORECASE):
        return False
    before = text[max(0, start - 10): start]
    after = text[end: end + 10]
    if _FOREIGN_CUR_BEFORE_RE.search(before):
        return True
    if _FOREIGN_CUR_AFTER_RE.match(after):
        return True
    return False


def _is_blocked_number(
    text: str,
    m: re.Match,
    amount: int,
    ignore_set: set[str],
) -> bool:
    start, end = m.start(), m.end()
    before_short = text[max(0, start - 10): start]
    after = text[end:]
    if _MODEL_PREFIX_RE.search(before_short):
        return True
    if _NON_PRICE_PREFIX_RE.search(before_short):
        return True
    if 1990 <= amount <= 2100 and (
        _YEAR_CONTEXT_RE.match(after) or _YEAR_PREFIX_RE.search(before_short)
    ):
        return True
    # 規則指定的裸型號數字或品牌代號小數字（若帶有貨幣符號或元/塊後綴則不阻擋）
    has_explicit_money = bool(m.group("cur") or m.group("unit") or m.group("suffix"))
    if not has_explicit_money:
        if amount < 100 and _BRAND_MODEL_PREFIX_RE.search(before_short):
            return True
        if ignore_set:
            raw_num = m.group("num").replace(",", "").strip()
            if raw_num in ignore_set or str(amount) in ignore_set:
                return True
    return False


def _has_blocked_suffix(text: str, m: re.Match, amount: int) -> bool:
    # 若本身已帶「元/塊/圓/台幣」明確貨幣後綴，後面緊跟「台北面交」「雙北」等絕非量詞
    if m.group("suffix"):
        return False
    after = text[m.end():]
    if _HARD_SUFFIX_BLOCK_RE.match(after):
        return True
    if _SLASH_DATE_RE.match(after) and (amount <= 31 or 1990 <= amount <= 2100):
        return True
    has_money_mark = bool(m.group("cur") or m.group("unit"))
    if not has_money_mark and amount < 1000 and _QUANTIFIER_SUFFIX_RE.match(after):
        return True
    return False


def _parse_at(text: str, start: int, ignore_set: set[str]):
    m = _SINGLE_RE.match(text, start)
    if not m:
        return None
    before_short = text[max(0, m.start() - 10): m.start()]
    amount = _convert(m.group("num"), m.group("unit"), m.group("sub"), before_short)
    if amount is None:
        return None
    if _has_blocked_suffix(text, m, amount):
        return None
    if _is_blocked_number(text, m, amount, ignore_set):
        return None
    return m, amount


_MALFORMED_COMMA_RE = re.compile(r"\d{1,3}(?:,\d{1,4})+")


def _normalize_comma_numbers(text: str) -> str:
    """修正 3,6000 這類逗號錯位寫法為 36000。"""
    def fix(match: re.Match) -> str:
        raw = match.group(0)
        parts = raw.split(",")
        if 1 <= len(parts[0]) <= 3 and all(len(p) == 3 for p in parts[1:]):
            return raw
        return raw.replace(",", "")

    return _MALFORMED_COMMA_RE.sub(fix, text)


def find_prices(
    text: str,
    min_amount: int = 1,
    max_amount: int = 10_000_000,
    ignore_numbers: Iterable[str] | None = None,
    allow_foreign: bool = False,
) -> list[PriceHit]:
    """回傳所有像價格的片段，支援區間價、箭頭降價、含運、外幣與佔位假價標記。"""
    if not text:
        return []
    text = _normalize_comma_numbers(text)
    ignore_set = {str(x).strip().replace(",", "") for x in (ignore_numbers or []) if str(x).strip()}
    hits: list[PriceHit] = []
    occupied: list[tuple[int, int]] = []

    for m in _SINGLE_RE.finditer(text):
        if any(s <= m.start() < e for s, e in occupied):
            continue
        if m.start() > 0:
            prev = text[m.start() - 1]
            if prev.isascii() and prev.isalnum():
                continue
        if _DATE_RE.match(text[m.start():]):
            continue

        before_short = text[max(0, m.start() - 12): m.start()]
        amount = _convert(m.group("num"), m.group("unit"), m.group("sub"), before_short)
        if amount is None or not (min_amount <= amount <= max_amount):
            continue
        if _has_blocked_suffix(text, m, amount):
            continue
        if _is_blocked_number(text, m, amount, ignore_set):
            continue

        is_foreign = _is_foreign_at(text, m.start(), m.end(), m.group("cur"))
        if is_foreign and not allow_foreign:
            occupied.append(m.span())
            continue

        strong = bool(m.group("cur") or m.group("unit") or m.group("suffix"))
        before = text[max(0, m.start() - 30): m.start()]
        after = text[m.end(): m.end() + 10]
        is_orig = bool(_ORIGINAL_PRICE_RE.search(before_short))

        # 箭頭降價：15000 -> 11500、15000→11500、15000降售11500
        drop_match = _ARROW_DROP_RE.match(text, m.end())
        if drop_match:
            second = _parse_at(text, drop_match.end(), ignore_set)
            if second:
                m2, amount2 = second
                if min_amount <= amount2 <= max_amount:
                    if not (_is_foreign_at(text, m2.start(), m2.end(), m2.group("cur")) and not allow_foreign):
                        # 原價保留標記為 is_original=True，新價標記為強訊號實際售價
                        hits.append(
                            PriceHit(
                                amount=amount,
                                raw=m.group(0).strip(),
                                strong=strong,
                                pos=m.start(),
                                span=(m.start(), m.end()),
                                high=amount,
                                includes_shipping=False,
                                confidence=0.4,
                                is_placeholder=is_placeholder_amount(amount),
                                is_original=True,
                                is_foreign=is_foreign,
                            )
                        )
                        includes2 = bool(_SHIP_RE.search(text[m2.end(): m2.end() + 8]))
                        hits.append(
                            PriceHit(
                                amount=amount2,
                                raw=m2.group(0).strip(),
                                strong=True,
                                pos=m2.start(),
                                span=(m2.start(), m2.end()),
                                high=amount2,
                                includes_shipping=includes2,
                                confidence=1.0,
                                is_placeholder=is_placeholder_amount(amount2),
                                is_original=False,
                                is_foreign=is_foreign,
                            )
                        )
                        occupied.append((m.start(), m2.end()))
                        continue

        # 區間價：5000-6000、5000~6000、5000至6000
        range_match = _RANGE_SEP_RE.match(text, m.end())
        if range_match:
            second = _parse_at(text, range_match.end(), ignore_set)
            if second:
                m2, amount2 = second
                if min_amount <= amount2 <= max_amount:
                    low, high = sorted((amount, amount2))
                    raw = text[m.start(): m2.end()].strip()
                    includes = bool(_SHIP_RE.search(text[m2.end(): m2.end() + 8]))
                    hits.append(
                        PriceHit(
                            amount=low,
                            raw=raw,
                            strong=True,
                            pos=m.start(),
                            span=(m.start(), m2.end()),
                            high=high,
                            is_range=True,
                            includes_shipping=includes,
                            confidence=0.85,
                            is_placeholder=False,
                            is_original=is_orig,
                            is_foreign=is_foreign,
                        )
                    )
                    occupied.append((m.start(), m2.end()))
                    continue

        if not strong and not _CONTEXT_RE.search(before) and not _CONTEXT_RE.search(after):
            if not (amount >= 500 and _AFTER_LOC_CONTEXT_RE.match(after)):
                continue
        includes = bool(_SHIP_RE.search(text[m.end(): m.end() + 8]))
        hits.append(
            PriceHit(
                amount=amount,
                raw=m.group(0).strip(),
                strong=strong,
                pos=m.start(),
                span=(m.start(), m.end()),
                high=amount,
                includes_shipping=includes,
                confidence=1.0 if strong else 0.6,
                is_placeholder=is_placeholder_amount(amount),
                is_original=is_orig,
                is_foreign=is_foreign,
            )
        )
        occupied.append(m.span())

    hits.sort(key=lambda h: (h.amount, h.upper, h.pos))
    # 同一位置不重複，保留不同位置（例如多品項同價或前後文不同）但保留既有去重語意
    seen: set[tuple[int, int, bool]] = set()
    unique: list[PriceHit] = []
    for hit in hits:
        key = (hit.amount, hit.upper, hit.is_original)
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)
    return unique
