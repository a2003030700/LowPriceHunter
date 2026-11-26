"""Facebook 反爬蟲亂碼清理與三層跨社團／同賣家去重引擎。

三層去重：
1. 淨化正文指紋（去除 FB 隱藏亂碼、社團標頭、時間標記、標點空白後的精確比對）
2. 同賣家＋同規則＋同價位冷卻窗（24 小時內相同價格或微幅加價 <= +5% 去重；若賣家降價仍照常通知）
3. 近 24 小時正文 2-gram 相似度比對（擋掉微調字句、加標籤或漏抓賣家名的跨社團轉貼）
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

# 移除 Facebook 混淆用不可見字元
_INVISIBLE_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff\u0300-\u036f\u00ad]")

# Facebook 常見的 timestamp 混淆亂碼（例如 notosSpdrel3tm8846886...剛m1）
_NOTOS_HEAD_RE = re.compile(
    r"notos[A-Za-z0-9]{6,}[^\n]{0,140}?(?=\s*分享對象：|\n|$)",
    re.IGNORECASE,
)
_NOTOS_TAIL_RE = re.compile(r"notos[A-Za-z0-9]{6,}[^\n]*", re.IGNORECASE)

# Facebook 圖片 alt 夾帶的長串隨機亂碼（例如 " Henry Chen 貼文的相片 ek5vJNM5Sdw..."）
_PHOTO_DECOY_WITH_NAME_RE = re.compile(
    r"(?:^|\s)[A-Za-z\u4e00-\u9fff]{1,20}(?:\s+[A-Za-z\u4e00-\u9fff]{1,20})?\s+貼文的相片\s+[A-Za-z0-9]{14,}[^\n]*"
)
_PHOTO_DECOY_RE = re.compile(r"\s*貼文的相片\s+[A-Za-z0-9]{14,}[^\n]*")

# 黏在數字或文字中間的假網域干擾字串（例如 10000GddqTZe2J.comHenry；必須以英文字母開頭避免吃掉價格數字）
_FAKE_COM_RE = re.compile(
    r"(?<![:/@.\w])([A-Za-z][A-Za-z0-9]{5,17})\.com\b|(?<=\d)([A-Za-z][A-Za-z0-9]{5,17})\.com"
)

# 24 字元以上純英數混雜的隨機干擾字串（非網址）
_LONG_RANDOM_TOKEN_RE = re.compile(r"(?<![:/@\w-])([A-Za-z0-9]{24,})(?![\w/-])")

_SAFE_DOMAINS = frozenset(
    {
        "facebook", "fb", "instagram", "youtube", "youtu", "google", "gmail",
        "yahoo", "shopee", "ruten", "pchome", "momo", "carousell", "imgur",
        "discord", "discordapp", "github", "apple", "sony", "playstation",
        "nintendo", "microsoft", "amazon", "twitter", "tiktok", "threads",
        "example", "line", "plurk", "mobile01", "gamer",
    }
)

_AUDIENCE_HEADER_RE = re.compile(
    r"分享對象：\s*(?:公開社團|私密社團|社團成員|公開|朋友|只限本人)\s*"
)
_CHROME_NOISE_RE = re.compile(
    r"(?:已驗證|精選貢獻者|社團專家|版主|管理員|·\s*追蹤|追蹤|貼文的相片|"
    r"賣家聯絡方式|傳送訊息|發訊息|查看更多|顯示更多|See more)"
)
_REL_TIME_RE = re.compile(
    r"(?:大約\s*)?(?:\d+\s*(?:分鐘|小時|天|日|週|星期|個月|年)前?|剛剛|昨天)"
)
_AUTHOR_BADGE_RE = re.compile(
    r"(?:已驗證|精選貢獻者|社團專家|版主|管理員|追蹤|·)"
)
_KEEP_NORM_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff\u3040-\u30ff]+")


def _looks_random_alnum(token: str, *, for_fake_com: bool = False) -> bool:
    """判斷英數字串是否像 Facebook 反爬蟲隨機亂碼（大小寫混雜且切換頻繁）。"""
    if not token:
        return False
    low = token.lower()
    if low in _SAFE_DOMAINS:
        return False
    has_upper = any("A" <= c <= "Z" for c in token)
    has_lower = any("a" <= c <= "z" for c in token)
    has_digit = any("0" <= c <= "9" for c in token)
    if not (has_upper and has_lower):
        return False
    # 計算大小寫／數字類型切換次數（自然字首大寫如 Google 僅切換 1 次，隨機碼至少 2 次以上）
    transitions = 0
    prev = None
    for c in token:
        kind = "u" if "A" <= c <= "Z" else ("l" if "a" <= c <= "z" else "d")
        if prev is not None and kind != prev:
            transitions += 1
        prev = kind
    if for_fake_com:
        return transitions >= 2
    if len(token) >= 24:
        return transitions >= 4 or (has_digit and transitions >= 3)
    return (has_digit and transitions >= 3) or transitions >= 4


def clean_fb_text(text: str) -> str:
    """清除 Facebook 隱藏 DOM 洩漏的不可見字元與隨機反爬蟲亂碼。"""
    if not text:
        return ""
    s = _INVISIBLE_RE.sub("", str(text))
    s = _NOTOS_HEAD_RE.sub(" ", s)
    s = _NOTOS_TAIL_RE.sub("", s)

    def _replace_fake_com(m: re.Match) -> str:
        tok = m.group(1) or m.group(2) or ""
        if _looks_random_alnum(tok, for_fake_com=True):
            return " "
        return m.group(0)

    s = _FAKE_COM_RE.sub(_replace_fake_com, s)
    s = _PHOTO_DECOY_WITH_NAME_RE.sub("", s)
    s = _PHOTO_DECOY_RE.sub("", s)

    def _replace_long_token(m: re.Match) -> str:
        tok = m.group(1) or ""
        if _looks_random_alnum(tok):
            return " "
        return m.group(0)

    s = _LONG_RANDOM_TOKEN_RE.sub(_replace_long_token, s)

    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines()]
    cleaned = "\n".join(ln for ln in lines if ln)
    return cleaned.strip()


def normalize_author(author: str) -> str:
    """正規化賣家名稱（去除徽章、亂碼、空白並轉小寫）。"""
    if not author:
        return ""
    s = clean_fb_text(author)
    s = unicodedata.normalize("NFKC", s)
    s = _AUTHOR_BADGE_RE.sub("", s)
    s = re.sub(r"\s+", "", s).lower()
    return s


def extract_core_body(text: str, author: str = "") -> str:
    """剝除社團標頭（作者名、分享對象、時間戳等），保留賣家撰寫的真實正文。"""
    s = clean_fb_text(text)
    if not s:
        return ""
    # 若有「分享對象：公開社團／私密社團」，優先切掉它以前的標頭區塊
    m = _AUDIENCE_HEADER_RE.search(s)
    if m and m.start() < 120:
        s = s[m.end():].strip()
    elif author:
        clean_author = clean_fb_text(author).strip()
        if clean_author and s.startswith(clean_author):
            s = s[len(clean_author):].strip()
    s = _AUDIENCE_HEADER_RE.sub(" ", s)
    s = _CHROME_NOISE_RE.sub(" ", s)
    s = _REL_TIME_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_for_dedup(text: str, author: str = "", listing_title: str = "") -> str:
    """轉成純中英數正規化字串，供指紋與相似度比對使用。"""
    core = extract_core_body(text, author=author)
    title = clean_fb_text(listing_title or "")
    combined = f"{title} {core}".strip() if title and title not in core else core
    combined = unicodedata.normalize("NFKC", combined).lower()
    return _KEEP_NORM_RE.sub("", combined)


def content_fingerprint(
    author: str, text: str, listing_title: str = ""
) -> str | None:
    """第一層：淨化後正文指紋（跨公開／私密社團完全一致即命中）。"""
    norm_body = normalize_for_dedup(text, author=author, listing_title=listing_title)
    if len(norm_body) < 10:
        return None
    norm_author = normalize_author(author)
    core = f"{norm_author}|{norm_body[:200]}"
    return "fp2:" + hashlib.sha1(core.encode("utf-8")).hexdigest()[:24]


def _bigrams(norm_text: str) -> set[str]:
    if len(norm_text) < 2:
        return {norm_text} if norm_text else set()
    return {norm_text[i : i + 2] for i in range(len(norm_text) - 1)}


def text_similarity(norm_a: str, norm_b: str) -> float:
    """以字元 2-gram Jaccard 計算兩篇正規化貼文的相似度（0.0 ~ 1.0）。"""
    if not norm_a or not norm_b:
        return 0.0
    if norm_a == norm_b:
        return 1.0
    a = _bigrams(norm_a[:300])
    b = _bigrams(norm_b[:300])
    if not a or not b:
        return 0.0
    union = len(a | b)
    return (len(a & b) / union) if union else 0.0


def find_cross_group_duplicate(
    author: str,
    rule_name: str,
    price: int | None,
    text: str,
    listing_title: str,
    recent_deals: list[dict],
    current_group: str = "",
) -> dict | None:
    """檢查近 24 小時已通知貼文，回傳命中的跨社團／同賣家重複資訊或 None。

    設計原則：
    - 若賣家「降價」重發（新價 < 舊價），一律放行通知，不漏掉降價撿便宜。
    - Layer 1：淨化後正文完全相同（含未抓到賣家名或換社團標頭的情況）。
    - Layer 2：同賣家＋同規則＋相同或微幅加價（<= +5%）冷卻去重。
    - Layer 3：同規則下正文 2-gram 高度相似（同賣家 >= 0.60；未確認賣家 >= 0.78）。
    """
    if not recent_deals:
        return None
    norm_author = normalize_author(author)
    norm_rule = (rule_name or "").strip().lower()
    norm_new = normalize_for_dedup(text, author=author, listing_title=listing_title)

    for deal in recent_deals:
        old_group = str(deal.get("group") or "")
        old_author_raw = str(deal.get("author") or "")
        old_author = normalize_author(old_author_raw)
        old_rule = str(deal.get("rule") or "").strip().lower()
        old_price = deal.get("price")
        old_text = str(deal.get("text") or "")
        norm_old = normalize_for_dedup(old_text, author=old_author_raw)

        same_group = bool(current_group and old_group and current_group == old_group)
        same_rule = bool(norm_rule and old_rule and norm_rule == old_rule)
        same_seller = bool(len(norm_author) >= 2 and norm_author == old_author)

        # 賣家降價重發一律放行通知
        is_price_drop = bool(price and old_price and price < old_price)
        if is_price_drop:
            continue

        # Layer 1：淨化正文完全相同
        if len(norm_new) >= 12 and norm_new == norm_old:
            prefix = "重複貼文" if same_group else "跨社團重複貼文"
            return {
                "deal": deal,
                "layer": 1,
                "cross_group": not same_group,
                "reason": f"{prefix}（已於「{old_group or '其他社團'}」通知）",
            }

        # Layer 2：同賣家＋同規則＋同價位（含微幅加價 <= 5%，例如他團含運價）
        if (
            same_seller
            and same_rule
            and price
            and old_price
            and old_price <= price <= int(old_price * 1.05)
        ):
            seller_label = author.strip() or old_author_raw.strip()
            prefix = "同賣家短期重複" if same_group else "同賣家跨社團重複"
            return {
                "deal": deal,
                "layer": 2,
                "cross_group": not same_group,
                "reason": (
                    f"{prefix}（{seller_label} 已於「{old_group or '其他社團'}」"
                    f"以 NT${old_price:,} 通知）"
                ),
            }

        # Layer 3：同規則文案高度相似（擋改幾個字、加 hashtag、換行或未抓到賣家名）
        if same_rule and len(norm_new) >= 14 and len(norm_old) >= 14:
            sim = text_similarity(norm_new, norm_old)
            threshold = 0.60 if same_seller else 0.78
            if sim >= threshold:
                pct = int(round(sim * 100))
                prefix = "相似重複貼文" if same_group else "跨社團相似貼文"
                return {
                    "deal": deal,
                    "layer": 3,
                    "cross_group": not same_group,
                    "reason": (
                        f"{prefix}（與「{old_group or '其他社團'}」相似度 {pct}%）"
                    ),
                }

    return None
