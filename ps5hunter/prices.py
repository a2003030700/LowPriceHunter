"""價格解析：支援貨幣、元/塊、萬/千/k、區間價、含運與可議。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_AMOUNT = r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
_UNIT = r"(?:萬|万|千|[kK])"
_CUR = r"(?:NT\s*\$|NTD|TWD|台幣|新台幣|\$|＄)"

_SINGLE_RE = re.compile(
    r"(?:(?P<cur>" + _CUR + r")\s*)?"
    r"(?P<num>" + _AMOUNT + r")"
    r"(?:\s*(?P<unit>" + _UNIT + r")(?P<sub>\d{1,4})?)?"
    r"(?:\s*(?P<suffix>元|塊|圓|台幣|TWD|NTD|NT\$))?",
    re.IGNORECASE,
)
_RANGE_SEP_RE = re.compile(r"\s*(?:-|–|—|~|～|至|到)\s*")
_SUFFIX_BLOCK_RE = re.compile(
    r"^(?:[A-Za-z]|年|月|日|號|型|吋|寸|GB|TB|Gb|Tb|g|G|代|版|成|樓|人|個|組|顆|片|次|%|％|/|:|："
    r"|台|支|張|套|件|隻|條|瓶|盒|包|罐|捲|雙|對|杯|本|份|間|棟|輛|部|艘|架)"
)
_MODEL_PREFIX_RE = re.compile(r"(?:CFI|CUH|型號|MODEL)\s*[-:]?\s*$", re.IGNORECASE)
_YEAR_CONTEXT_RE = re.compile(r"^\s*(?:年|購入|購買|買入|購自|出廠|製造|公司貨|保固)")
_DATE_RE = re.compile(r"^(?:19|20)\d{2}\s*[-/.]\s*\d{1,2}")
_CONTEXT_RE = re.compile(
    r"售|賣|價|降|出清|出價|面交|自取|含運|運費|可議|開價|折|便宜|收購|預算|費用|元|塊"
)
_SHIP_RE = re.compile(r"含運|含運費|運費內含|免運")

# 使用者常把「36,000」打成「3,6000」。這種逗號分組不合法（逗號後不是 3 位），
# 直接移除逗號會得到 36000，比取前段 3,600 更接近原意。
_MALFORMED_COMMA_RE = re.compile(r"\d{1,3}(?:,\d{1,4})+")


def _normalize_comma_numbers(text: str) -> str:
    def fix(match: re.Match) -> str:
        raw = match.group(0)
        parts = raw.split(",")
        if 1 <= len(parts[0]) <= 3 and all(len(p) == 3 for p in parts[1:]):
            return raw
        return raw.replace(",", "")

    return _MALFORMED_COMMA_RE.sub(fix, text)


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

    @property
    def low(self) -> int:
        return self.amount

    @property
    def upper(self) -> int:
        return self.high if self.high is not None else self.amount


def _convert(num: str, unit: str | None, sub: str | None) -> int | None:
    raw = num.replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    if unit in ("萬", "万"):
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
    elif unit in ("千",):
        value *= 1_000
        if sub:
            sub_value = int(sub)
            if len(sub) == 1:
                value += sub_value * 100
            elif len(sub) == 2:
                value += sub_value * 10
            elif len(sub) == 3:
                value += sub_value
    elif unit in ("k", "K"):
        value *= 1_000
    if value <= 0:
        return None
    return int(round(value))


def _parse_at(text: str, start: int):
    m = _SINGLE_RE.match(text, start)
    if not m:
        return None
    if _SUFFIX_BLOCK_RE.match(text[m.end():]):
        return None
    amount = _convert(m.group("num"), m.group("unit"), m.group("sub"))
    if amount is None:
        return None
    if _MODEL_PREFIX_RE.search(text[max(0, m.start() - 8):m.start()]):
        return None
    if 1990 <= amount <= 2100 and _YEAR_CONTEXT_RE.match(text[m.end():]):
        return None
    return m, amount


def find_prices(
    text: str, min_amount: int = 1, max_amount: int = 10_000_000
) -> list[PriceHit]:
    """回傳所有像價格的片段，支援區間價與含運。"""
    if not text:
        return []
    text = _normalize_comma_numbers(text)
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
        if _SUFFIX_BLOCK_RE.match(text[m.end():]):
            continue
        amount = _convert(m.group("num"), m.group("unit"), m.group("sub"))
        if amount is None or not (min_amount <= amount <= max_amount):
            continue
        if _MODEL_PREFIX_RE.search(text[max(0, m.start() - 8):m.start()]):
            continue
        if 1990 <= amount <= 2100 and _YEAR_CONTEXT_RE.match(text[m.end():]):
            continue

        strong = bool(m.group("cur") or m.group("unit") or m.group("suffix"))
        before = text[max(0, m.start() - 20): m.start()]
        after = text[m.end(): m.end() + 8]

        # 區間價：5000-6000、5000~6000、5000至6000
        range_match = _RANGE_SEP_RE.match(text, m.end())
        if range_match:
            second = _parse_at(text, range_match.end())
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
                        )
                    )
                    occupied.append((m.start(), m2.end()))
                    continue

        if not strong and not _CONTEXT_RE.search(before) and not _CONTEXT_RE.search(after):
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
            )
        )
        occupied.append(m.span())

    hits.sort(key=lambda h: (h.amount, h.upper, h.pos))
    seen: set[tuple[int, int]] = set()
    unique: list[PriceHit] = []
    for hit in hits:
        key = (hit.amount, hit.upper)
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)
    return unique
