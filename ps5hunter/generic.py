"""通用監控規則：關鍵字、必要詞、排除詞、價格區間與位置判斷。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .prices import find_prices

# 不論使用者有沒有填排除詞，一律視為「收購/徵求」的寫法
_BUYING_RE = re.compile(
    r"[\[【(（]\s*收\s*[\]】)）]|收購|誠收|徵收|想收|徵求|誠徵|求購|想買|預算"
)
_ORIGINAL_PRICE_RE = re.compile(
    r"(?:原價|原售價|原售|市價|定價|建議售價|原廠價)\s*[:：]?\s*$"
)

DEFAULT_BUNDLE_WORDS = ["合售", "整組", "全套", "打包", "一起帶", "含運"]

DEFAULT_EXCLUDES = ["已售出", "售出", "已保留", "已成交", "sold", "已下架"]

RISK_WORDS = ["預付", "先匯款", "貨到付款", "代購", "急售"]


def find_risk_words(text: str) -> list[str]:
    low = (text or "").lower()
    return [word for word in RISK_WORDS if word.lower() in low]


@dataclass
class Rule:
    name: str
    keywords: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    require: list[str] = field(default_factory=list)
    min_price: int = 0
    max_price: int = 100000
    price_near_keyword: bool = False
    near_distance: int = 60
    reject_multi_item: bool = False
    bundle_words: list[str] = field(default_factory=lambda: list(DEFAULT_BUNDLE_WORDS))

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


def rule_from_dict(raw: dict) -> Rule:
    bundle = [str(x).strip() for x in raw.get("bundle_words", []) if str(x).strip()]
    return Rule(
        name=str(raw.get("name") or "物品").strip(),
        keywords=[str(x).strip() for x in raw.get("keywords", []) if str(x).strip()],
        exclude=[str(x).strip() for x in raw.get("exclude", []) if str(x).strip()],
        require=[str(x).strip() for x in raw.get("require", []) if str(x).strip()],
        min_price=int(raw.get("min_price") or 0),
        max_price=int(raw.get("max_price") or 100000),
        price_near_keyword=bool(raw.get("price_near_keyword", False)),
        near_distance=int(raw.get("near_distance") or 60),
        reject_multi_item=bool(raw.get("reject_multi_item", False)),
        bundle_words=bundle or list(DEFAULT_BUNDLE_WORDS),
    )


def _min_distance(text: str, price, anchors: list[str]) -> int | None:
    """價格與最近產品字之間隔了幾個字。"""
    p_start = price.pos
    p_end = price.pos + len(price.raw)
    best: int | None = None
    for anchor in anchors:
        if not anchor:
            continue
        for m in re.finditer(re.escape(anchor), text, re.IGNORECASE):
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


def _span_of(text: str, word: str) -> list[int] | None:
    m = re.search(re.escape(word), text, re.IGNORECASE)
    return list(m.span()) if m else None


def _is_original_price(text: str, price) -> bool:
    before = text[max(0, price.pos - 12):price.pos]
    return bool(_ORIGINAL_PRICE_RE.search(before))


def analyze_detail(text: str, rule: Rule) -> dict:
    """完整分析，回傳可顯示在「規則測試器」的細節。"""
    detail = {
        "matched": False,
        "reason": "",
        "keyword": None,
        "require_hit": None,
        "excluded_word": None,
        "price": None,
        "price_high": None,
        "price_raw": None,
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
        "risk_words": [],
    }

    def gate(name: str, ok: bool, info: str = "") -> None:
        detail["gates"].append({"name": name, "ok": bool(ok), "detail": info})

    if not text:
        gate("內容", False, "空白")
        detail["reason"] = "空白內容"
        return detail

    low = text.lower()
    detail["risk_words"] = find_risk_words(text)

    if _BUYING_RE.search(text):
        gate("收購/徵求", False, "命中")
        detail["reason"] = "收購/徵求文"
        return detail
    gate("收購/徵求", True)

    for word in rule.exclude:
        if word.lower() in low:
            detail["excluded_word"] = word
            detail["spans"]["exclude"] = _span_of(text, word)
            gate("排除詞", False, word)
            detail["reason"] = f"含排除詞「{word}」"
            return detail
    gate("排除詞", True)

    if rule.require:
        hit = next((w for w in rule.require if w.lower() in low), None)
        if not hit:
            gate("必須包含", False, "缺少")
            detail["reason"] = "缺少必要關鍵字（可能只是平台/周邊）"
            return detail
        detail["require_hit"] = hit
        detail["spans"]["require"] = _span_of(text, hit)
        gate("必須包含", True, hit)
    else:
        gate("必須包含", True, "未設定")

    keyword = next((k for k in rule.keywords if k.lower() in low), None)
    if not keyword:
        gate("關鍵字", False, "缺少")
        detail["reason"] = "沒有產品關鍵字"
        return detail
    detail["keyword"] = keyword
    detail["spans"]["keyword"] = _span_of(text, keyword)
    gate("關鍵字", True, keyword)

    prices = find_prices(text)
    detail["price_count"] = len(prices)
    detail["price_candidates"] = [
        {
            "low": p.low,
            "high": p.upper,
            "raw": p.raw,
            "span": list(p.span) if p.span else None,
            "strong": p.strong,
            "confidence": p.confidence,
        }
        for p in prices
    ]
    if not prices:
        gate("價格", False, "找不到")
        detail["reason"] = "找不到價格"
        return detail
    gate("價格", True, f"{len(prices)} 個候選")

    # 區間重疊、邊界包含：貼文低價 <= 你的上限，且貼文高價 >= 你的下限
    in_range = [
        p for p in prices if p.low <= rule.max_price and p.upper >= rule.min_price
    ]
    if not in_range:
        best_all = max(prices, key=lambda p: (p.upper, p.low))
        detail["price"] = best_all.low
        detail["price_high"] = best_all.upper if best_all.is_range else None
        detail["spans"]["price"] = list(best_all.span) if best_all.span else None
        gate("價格區間", False, "沒有重疊")
        detail["reason"] = "價格不在設定區間"
        return detail
    gate("價格區間", True, f"{rule.min_price:,}–{rule.max_price:,}")

    # 原價／市價只是參考，不拿來當售價；有實際售價時優先用它
    non_original = [p for p in in_range if not _is_original_price(text, p)]
    if non_original:
        in_range = non_original

    # 一次列很多價格，又沒有「合售／整組」等字，通常是商品清單
    # 用全部非原價的價格計數，避免只有 1-2 個落在區間時漏掉清單
    plausible = [
        p for p in prices if p.upper >= 100 and not _is_original_price(text, p)
    ]
    if rule.reject_multi_item and len(plausible) >= 3 and not any(
        word.lower() in low for word in rule.bundle_words
    ):
        detail["multi_item"] = True
        detail["price"] = max(in_range, key=lambda p: (p.upper, p.low)).low
        gate("多項商品", False, f"{len(plausible)} 個價格")
        detail["reason"] = f"多項商品清單（{len(plausible)} 個價格）"
        return detail
    gate("多項商品", True, "未觸發" if rule.reject_multi_item else "未啟用")

    strong = [p for p in in_range if p.strong]
    pool = strong or in_range
    best = max(pool, key=lambda p: (p.upper, p.low))
    detail["price"] = best.low
    detail["price_high"] = best.upper if best.is_range else None
    detail["price_raw"] = best.raw
    detail["price_is_range"] = best.is_range
    detail["price_includes_shipping"] = best.includes_shipping
    detail["price_confidence"] = best.confidence
    detail["spans"]["price"] = (
        list(best.span) if best.span else [best.pos, best.pos + len(best.raw)]
    )

    anchors = rule.require or rule.keywords
    distance = _min_distance(text, best, anchors)
    detail["price_distance"] = distance
    if rule.price_near_keyword:
        ok = distance is not None and distance <= rule.near_distance
        gate("價格靠近", ok, f"距 {distance} 字（上限 {rule.near_distance}）")
        if not ok:
            shown = distance if distance is not None else "?"
            detail["reason"] = f"價格離產品字太遠（{shown} 字）"
            return detail
    else:
        gate("價格靠近", True, "未啟用")

    detail["matched"] = True
    detail["reason"] = "符合"
    gate("結果", True, "符合")
    return detail


def analyze_text(text: str, rule: Rule) -> Match:
    detail = analyze_detail(text, rule)
    return Match(
        matched=bool(detail["matched"]),
        keyword=detail["keyword"],
        price=detail["price"],
        price_high=detail.get("price_high"),
        risk_words=detail.get("risk_words") or [],
        reason=str(detail["reason"]),
    )
