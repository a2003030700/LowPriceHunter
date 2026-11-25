"""用 Playwright 驅動真實瀏覽器，讀取已加入社團的貼文。

Facebook 2025 後的社團版面把貼文永久連結從 DOM 拿掉了，因此改用兩個線索：
1. 貼文卡片內的相片連結 `?...&set=gm.<post_id>` → 直接組出 /groups/<gid>/posts/<pid>/
2. 沒有相片 ID 的貼文 → 點「分享」對話框，從中取出 /share/p/<code>/ 短連結

另外，臉書的動態消息是虛擬清單：捲動後舊卡片會被移除，所以每抓到一批卡片，
就要「立刻」處理（必要時開分享框取連結），不能等全部捲完才回頭點。
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import BrowserContext, Page, sync_playwright


class AuthError(RuntimeError):
    """未登入或登入態失效。"""


class CheckpointError(RuntimeError):
    """Facebook 跳出驗證碼／帳號檢查。"""


class LayoutError(RuntimeError):
    """頁面結構讀不到（版面改版或被擋）。"""


class GroupPausedError(LayoutError):
    """社團已被管理員暫停使用。"""


_ACTION_LABEL_SUFFIX = "這則貼文採取的動作"
_BLOCK_TEXTS = ("你暫時被封鎖", "帳號已被封鎖", "We suspended your account", "確認你的身分")
_PAUSED_GROUP_TEXTS = ("此社團已暫停", "管理員已暫停此社團", "This group is paused")

# 這些通知不是「有新貼文」，不需要觸發掃描
_IGNORE_NOTIF_PREFIXES = (
    "group_r2j", "group_request", "group_invited", "group_admin", "group_member",
    "group_join", "group_suggest", "group_creation", "group_approved", "group_declined",
    "group_left", "group_removed", "group_rules", "group_pending",
)

# 移除 Facebook 用來混淆時間戳的不可見字元
_INVISIBLE_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff\u0300-\u036f\u00ad]")

_CARDS_JS = """
() => {
  const clean = s => (s || '')
    .replace(/[\\u200b-\\u200f\\u2060\\ufeff\\u0300-\\u036f\\u00ad]/g, '')
    .trim();
  const visibleText = (node) => {
    if (!node) return '';
    const clone = node.cloneNode(true);
    clone.querySelectorAll('[aria-hidden="true"]').forEach(n => n.remove());
    return clean(clone.innerText || '');
  };
  const extractBodyAndComments = (card) => {
    const authorComments = [];
    const commentNodes = Array.from(card.querySelectorAll('div[role="article"]'));
    for (const cn of commentNodes) {
      const lbl = cn.getAttribute('aria-label') || '';
      const txt = visibleText(cn);
      if (txt && (txt.includes('作者') || lbl.includes('作者'))) {
        authorComments.push(txt.slice(0, 300));
      }
    }
    const clone = card.cloneNode(true);
    clone.querySelectorAll('[aria-hidden="true"]').forEach(n => n.remove());
    clone.querySelectorAll('div[role="article"]').forEach(n => n.remove());
    let raw = (clone.innerText || '')
      .replace(/[\\u200b-\\u200f\\u2060\\ufeff\\u0300-\\u036f\\u00ad]/g, '');
    const cutMarkers = ['查看更多留言', '查看全部留言', '撰寫公開留言', '以 ', '最相關', '所有留言'];
    for (const mk of cutMarkers) {
      const idx = raw.indexOf('\\n' + mk);
      if (idx > 20) raw = raw.slice(0, idx);
    }
    return { text: raw.trim(), authorComments };
  };
  const extractTime = (card) => {
    for (const a of card.querySelectorAll('a[aria-label]')) {
      const lbl = clean(a.getAttribute('aria-label') || '');
      if (!lbl || lbl.length > 24) continue;
      if (/^(大約\\s*)?(\\d+\\s*(分鐘|小時|天|日|週|星期|個月|年)|昨天|剛剛|\\d{1,2}月\\d{1,2}日)/.test(lbl)) {
        return lbl;
      }
    }
    for (const e of card.querySelectorAll('span,div,a')) {
      if (e.children.length > 2) continue;
      if (e.getAttribute('aria-hidden') === 'true') continue;
      const st = window.getComputedStyle(e);
      if (st && (st.display === 'none' || st.visibility === 'hidden' || st.fontSize === '0px')) continue;
      if (e.offsetWidth === 0 && e.offsetHeight === 0) continue;
      const t = clean(e.innerText || '');
      if (!t || t.length > 16) continue;
      if (/^(大約\\s*)?(\\d+\\s*(分鐘|小時|天|日|週|星期|個月|年)|昨天|剛剛|\\d{1,2}月\\d{1,2}日)前?$/.test(t)) {
        return t;
      }
    }
    return '';
  };
  const extractListing = (card) => {
    let listingTitle = '';
    let listingPrice = '';
    let listingOrigPrice = '';
    let listingStatus = '';
    const commerceLink = card.querySelector('a[href*="/commerce/listing/"], a[href*="product_id="]');
    const scope = commerceLink ? (commerceLink.closest('div') || commerceLink) : card;
    const scopeText = visibleText(scope);
    const statusMatch = scopeText.match(/(已售出|缺貨|已下架|OUT OF STOCK|SOLD)/i);
    if (statusMatch) listingStatus = statusMatch[1];
    for (const el of scope.querySelectorAll('del, s, span')) {
      const st = window.getComputedStyle(el);
      if (el.tagName === 'DEL' || el.tagName === 'S' || (st && (st.textDecorationLine || '').includes('line-through'))) {
        const t = clean(el.innerText || '');
        if (/^(NT\\$?|\\$)[\\d,]+/.test(t)) { listingOrigPrice = t; break; }
      }
    }
    if (commerceLink) {
      const lines = visibleText(commerceLink).split(/\\n+/).map(x => x.trim()).filter(Boolean);
      for (const ln of lines) {
        if (!listingPrice && (/^(NT\\$?|\\$)[\\d,]+/.test(ln) || ln === '免費')) {
          listingPrice = ln;
        } else if (!listingTitle && !/^(已售出|缺貨|發訊息|傳送訊息)$/.test(ln) && ln.length >= 2) {
          listingTitle = ln;
        }
      }
    }
    return { listingTitle, listingPrice, listingOrigPrice, listingStatus };
  };
  const out = [];
  const buttons = Array.from(document.querySelectorAll('div[role="button"][aria-label]'))
    .filter(b => (b.getAttribute('aria-label') || '').endsWith('%s'));
  let i = 0;
  for (const eb of buttons) {
    let card = eb;
    for (let k = 0; k < 12 && card; k++) {
      if (card.querySelector('div[role="button"][aria-label*="分享"]')) break;
      card = card.parentElement;
    }
    if (!card) continue;
    const { text, authorComments } = extractBodyAndComments(card);
    const age = extractTime(card);
    const listing = extractListing(card);
    let author = '';
    const headLink = card.querySelector('h2 a, h3 a, h4 a, strong a, strong');
    if (headLink) author = clean(headLink.innerText || '').slice(0, 60);
    const a = card.querySelector('a[href*="set=gm."]');
    let pid = null;
    if (a) { const m = a.href.match(/gm\\.(\\d+)/); if (m) pid = m[1]; }
    let commerce = null;
    const cl = card.querySelector('a[href*="/commerce/listing/"]');
    if (cl) { const m = cl.href.match(/commerce\\/listing\\/(\\d+)/); if (m) commerce = m[1]; }
    if (!commerce) {
      const mp = card.querySelector('a[href*="product_id="]');
      if (mp) { const m = mp.href.match(/product_id=(\\d+)/); if (m) commerce = m[1]; }
    }
    out.push({
      id: i,
      text: text,
      author: author,
      author_comments: authorComments,
      listing_title: listing.listingTitle,
      listing_price: listing.listingPrice,
      listing_original_price: listing.listingOrigPrice,
      listing_status: listing.listingStatus,
      pid: pid,
      commerce: commerce,
      age: age
    });
    i++;
  }
  return out;
}
""" % _ACTION_LABEL_SUFFIX

_CLICK_SHARE_JS = """
(idx) => {
  const bs = Array.from(document.querySelectorAll('div[role="button"][aria-label]'))
    .filter(b => (b.getAttribute('aria-label') || '').endsWith('%s'));
  const eb = bs[idx];
  if (!eb) return false;
  let card = eb;
  for (let k = 0; k < 12 && card; k++) {
    if (card.querySelector('div[role="button"][aria-label*="分享"]')) break;
    card = card.parentElement;
  }
  if (!card) return false;
  const share = card.querySelector('div[role="button"][aria-label*="分享"]');
  if (!share) return false;
  share.click();
  return true;
}
""" % _ACTION_LABEL_SUFFIX

_FIND_CARD_JS = """
(snip) => {
  const bs = Array.from(document.querySelectorAll('div[role="button"][aria-label]'))
    .filter(b => (b.getAttribute('aria-label') || '').endsWith('%s'));
  let i = 0;
  for (const eb of bs) {
    let card = eb;
    for (let k = 0; k < 12 && card; k++) {
      if (card.querySelector('div[role="button"][aria-label*="分享"]')) break;
      card = card.parentElement;
    }
    if (!card) { i++; continue; }
    const c = card.cloneNode(true);
    c.querySelectorAll('[aria-hidden="true"]').forEach(n => n.remove());
    const t = (c.innerText || '').replace(/[\\u200b-\\u200f\\u2060\\ufeff\\u0300-\\u036f\\u00ad]/g, '');
    if (t.includes(snip)) return i;
    i++;
  }
  return null;
}
""" % _ACTION_LABEL_SUFFIX

_EXPAND_CARD_JS = """
(idx) => {
  const bs = Array.from(document.querySelectorAll('div[role="button"][aria-label]'))
    .filter(b => (b.getAttribute('aria-label') || '').endsWith('%s'));
  const eb = bs[idx];
  if (!eb) return false;
  let card = eb;
  for (let k = 0; k < 12 && card; k++) {
    if (card.querySelector('div[role="button"][aria-label*="分享"]')) break;
    card = card.parentElement;
  }
  if (!card) return false;
  const nodes = Array.from(card.querySelectorAll('div[role="button"], span[role="button"]'));
  const more = nodes.find(n => /^(查看更多|顯示更多|See more)$/.test((n.innerText || '').trim()));
  if (!more) return false;
  more.click();
  return true;
}
""" % _ACTION_LABEL_SUFFIX

_CARD_TEXT_JS = """
(idx) => {
  const bs = Array.from(document.querySelectorAll('div[role="button"][aria-label]'))
    .filter(b => (b.getAttribute('aria-label') || '').endsWith('%s'));
  const eb = bs[idx];
  if (!eb) return '';
  let card = eb;
  for (let k = 0; k < 12 && card; k++) {
    if (card.querySelector('div[role="button"][aria-label*="分享"]')) break;
    card = card.parentElement;
  }
  if (!card) return '';
  const clone = card.cloneNode(true);
  clone.querySelectorAll('[aria-hidden="true"]').forEach(n => n.remove());
  clone.querySelectorAll('div[role="article"]').forEach(n => n.remove());
  let raw = (clone.innerText || '').replace(/[\\u200b-\\u200f\\u2060\\ufeff\\u0300-\\u036f\\u00ad]/g, '');
  for (const mk of ['查看更多留言', '查看全部留言', '撰寫公開留言', '以 ', '最相關', '所有留言']) {
    const pos = raw.indexOf('\\n' + mk);
    if (pos > 20) raw = raw.slice(0, pos);
  }
  return raw.trim();
}
""" % _ACTION_LABEL_SUFFIX


_CLICK_COMMENT_JS = """
(idx) => {
  const bs = Array.from(document.querySelectorAll('div[role="button"][aria-label]'))
    .filter(b => (b.getAttribute('aria-label') || '').endsWith('%s'));
  const eb = bs[idx];
  if (!eb) return false;
  let card = eb;
  for (let k = 0; k < 12 && card; k++) {
    if (card.querySelector('div[role="button"][aria-label*="分享"]')) break;
    card = card.parentElement;
  }
  if (!card) return false;
  const btn = Array.from(card.querySelectorAll('div[role="button"][aria-label]'))
    .find(b => {
      const l = b.getAttribute('aria-label') || '';
      return l.includes('回應') && l.includes('貼文');
    });
  if (!btn) return false;
  btn.click();
  return true;
}
""" % _ACTION_LABEL_SUFFIX


def _post_text_from_title(title: str) -> str:
    title = (title or "").strip()
    for suffix in (" | Facebook", " - Facebook"):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    parts = [p.strip() for p in title.split(" | ") if p.strip()]
    if len(parts) >= 2:
        body = " | ".join(parts[1:])
        if body:
            return body
    return title


def _relative_days(text: str) -> float | None:
    """把「3小時前」「2天前」「11月24日」轉成天數。"""
    if not text:
        return None
    if "剛剛" in text:
        return 0.0
    if "昨天" in text:
        return 1.0
    m = re.search(r"(\d+)\s*(分鐘|小時|天|日|週|星期|個月|月|年)", text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "分鐘":
            return n / 1440.0
        if unit == "小時":
            return n / 24.0
        if unit in ("天", "日"):
            return float(n)
        if unit in ("週", "星期"):
            return n * 7.0
        if unit in ("個月", "月"):
            return n * 30.0
        if unit == "年":
            return n * 365.0
    md = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if md:
        now = datetime.now()
        try:
            dt = datetime(now.year, int(md.group(1)), int(md.group(2)))
            if dt > now + timedelta(days=2):
                dt = dt.replace(year=now.year - 1)
            return max(0.0, (now - dt).total_seconds() / 86400.0)
        except ValueError:
            return None
    return None


def _body_snippet(text: str, length: int = 24) -> str:
    """取出貼文正文的開頭，用來在 DOM 裡重新找回同一張卡片。

    卡片文字開頭常是「作者 + 已驗證 + 分享對象：…」，直接取前 36 字會對不上，
    所以先切掉「分享對象：」之前的部分。
    """
    m = re.search(r"分享對象：[^【\[]*", text)
    body = text[m.end():] if m else text
    snippet = body.strip()[:length].strip()
    if len(snippet) >= 6:
        return snippet
    return text[12:12 + length].strip()


@dataclass
class Post:
    key: str
    group_name: str
    group_id: str
    text: str
    post_id: str | None
    url: str | None
    card_id: int | None = None
    age_minutes: float | None = None
    created_at: datetime | None = None
    author: str = ""
    listing_title: str = ""
    listing_price: str = ""
    listing_original_price: str = ""
    listing_status: str = ""
    author_comments: list[str] = field(default_factory=list)

    @property
    def short_text(self) -> str:
        prefix = f"{self.listing_title} {self.listing_price} ".strip()
        full = f"{prefix}｜{self.text}" if prefix and prefix not in self.text else self.text
        return re.sub(r"\s+", " ", full).strip()


class FBScraper:
    def __init__(
        self,
        profile_dir: Path,
        channel: str = "msedge",
        headless: bool = True,
        timezone: str = "Asia/Taipei",
        slow_mo: int = 0,
    ):
        self.profile_dir = Path(profile_dir)
        self.channel = channel
        self.headless = headless
        self.timezone = timezone
        self.slow_mo = slow_mo
        self._pw = None
        self.ctx: BrowserContext | None = None
        self.page: Page | None = None

    # ------------------------------------------------------------ lifecycle
    def __enter__(self) -> "FBScraper":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        # 部分 Windows 環境（含沙箱/防毒）會禁止瀏覽器寫入系統 TEMP，
        # 這裡把暫存目錄導到專案內的 data/tmp。
        tmp_dir = self.profile_dir.parent / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        for var in ("TEMP", "TMP", "TMPDIR"):
            os.environ[var] = str(tmp_dir)
        self._pw = sync_playwright().start()
        kwargs = dict(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            locale="zh-TW",
            timezone_id=self.timezone,
            viewport={"width": 1400, "height": 950},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-notifications",
            ],
            ignore_default_args=["--enable-automation"],
            slow_mo=self.slow_mo,
        )
        if self.channel:
            kwargs["channel"] = self.channel
        self.ctx = self._pw.chromium.launch_persistent_context(**kwargs)
        self.ctx.set_default_timeout(15_000)
        self.ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.ctx:
                self.ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    # ------------------------------------------------------------ auth
    def is_logged_in(self) -> bool:
        assert self.ctx
        cookies = self.ctx.cookies("https://www.facebook.com")
        return any(c.get("name") == "c_user" and c.get("value") for c in cookies)

    def wait_for_login(self, timeout_seconds: int = 600) -> None:
        """開一個有畫面的瀏覽器，等使用者自己完成 Facebook 登入。"""
        assert self.page
        self.page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
        deadline = time.time() + timeout_seconds
        last_report = 0
        while time.time() < deadline:
            if self.is_logged_in():
                time.sleep(2)
                return
            if time.time() - last_report > 15:
                print("… 請在瀏覽器視窗中登入 Facebook（完成後會自動繼續）", flush=True)
                last_report = time.time()
            time.sleep(2)
        raise AuthError("等待登入逾時，請重新執行 login。")

    # ------------------------------------------------------------ helpers
    def _check_blocked(self, page: Page) -> None:
        url = page.url or ""
        if "checkpoint" in url:
            raise CheckpointError("Facebook 跳出帳號檢查，請先用一般瀏覽器處理。")
        if "/login" in url and not self.is_logged_in():
            raise AuthError("Facebook 要求重新登入。")
        try:
            body = page.inner_text("body", timeout=3000)
        except Exception:
            return
        if any(t in body for t in _BLOCK_TEXTS):
            raise CheckpointError("Facebook 跳出帳號檢查／驗證碼，請先用一般瀏覽器處理。")

    def _dismiss_popups(self, page: Page) -> None:
        for label in ("允許所有 Cookie", "Allow all cookies", "現在不要", "Not now", "稍後再說"):
            try:
                btn = page.get_by_role("button", name=label, exact=False).first
                if btn.is_visible(timeout=400):
                    btn.click(timeout=800)
                    time.sleep(0.2)
            except Exception:
                pass
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

    def _resolve_group_url(self, url: str) -> str:
        ident = url.split("/groups/", 1)[1].split("/")[0].split("?")[0]
        return f"https://www.facebook.com/groups/{ident}/?sorting_setting=CHRONOLOGICAL"

    # ------------------------------------------------------------ scraping
    def scrape_group(
        self,
        group_name: str,
        group_url: str,
        max_posts: int = 80,
        resolve_when: Callable[[str], bool] | None = None,
        resolve_when_post: Callable[[Post], bool] | None = None,
        expand_keywords: list[str] | None = None,
        stop_at_post_id: str | None = None,
        stop_at_ts: datetime | None = None,
        max_scrolls: int | None = None,
    ) -> list[Post]:
        assert self.page
        page = self.page
        group_id = group_url.split("/groups/", 1)[1].split("/")[0].split("?")[0]

        page.goto(self._resolve_group_url(group_url), wait_until="domcontentloaded", timeout=60_000)
        time.sleep(1.5)
        self._check_blocked(page)
        self._dismiss_popups(page)
        try:
            head_body = page.inner_text("body", timeout=3000)[:2000]
            if any(marker in head_body for marker in _PAUSED_GROUP_TEXTS):
                raise GroupPausedError(f"社團已被管理員暫停（{group_name}）")
        except GroupPausedError:
            raise
        except Exception:
            pass

        try:
            page.wait_for_selector(
                f'div[role="button"][aria-label$="{_ACTION_LABEL_SUFFIX}"]', timeout=20_000
            )
        except Exception:
            self._check_blocked(page)
            body = ""
            try:
                body = page.inner_text("body")[:300]
            except Exception:
                pass
            if any(marker in body for marker in _PAUSED_GROUP_TEXTS):
                raise GroupPausedError(f"社團已被管理員暫停（{group_name}）")
            raise LayoutError(f"讀不到社團貼文（{group_name}）。頁面片段：{body!r}")

        collected: dict[str, Post] = {}
        last_count = -1
        last_collected = -1
        stall = 0
        expanded = 0
        reached = False
        scrolls = max_scrolls if max_scrolls is not None else min(120, max(20, max_posts // 4))

        for _step in range(scrolls):
            cards = page.evaluate(_CARDS_JS)
            for item in cards:
                text = _INVISIBLE_RE.sub("", item.get("text") or "").strip()
                if not text or len(text) > 6000:
                    continue
                if expanded < 60:
                    new_text = self._expand_see_more(
                        page, item.get("id"), text, expand_keywords=expand_keywords
                    )
                    if new_text != text:
                        text = new_text
                        expanded += 1
                pid = item.get("pid")
                commerce = item.get("commerce")
                if pid:
                    url = f"https://www.facebook.com/groups/{group_id}/posts/{pid}/"
                    key = f"{group_id}_{pid}"
                elif commerce:
                    url = f"https://www.facebook.com/commerce/listing/{commerce}/"
                    key = f"{group_id}_c{commerce}"
                else:
                    url = None
                    key = "h_" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]
                if key in collected:
                    continue
                age_days = _relative_days(item.get("age") or "")
                age_minutes = age_days * 1440 if age_days is not None else None
                created_at = (
                    datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
                    if age_minutes is not None
                    else None
                )
                if stop_at_post_id and pid and pid == stop_at_post_id:
                    reached = True
                    break
                if (
                    stop_at_ts is not None
                    and created_at is not None
                    and created_at <= stop_at_ts
                ):
                    reached = True
                    break
                post = Post(
                    key=key,
                    group_name=group_name,
                    group_id=group_id,
                    text=text,
                    post_id=pid,
                    url=url,
                    card_id=item.get("id"),
                    age_minutes=age_minutes,
                    created_at=created_at,
                    author=str(item.get("author") or ""),
                    listing_title=str(item.get("listing_title") or ""),
                    listing_price=str(item.get("listing_price") or ""),
                    listing_original_price=str(item.get("listing_original_price") or ""),
                    listing_status=str(item.get("listing_status") or ""),
                    author_comments=[
                        str(c) for c in (item.get("author_comments") or []) if str(c).strip()
                    ],
                )
                collected[key] = post
                should_resolve = False
                if url is None:
                    try:
                        if resolve_when_post is not None:
                            should_resolve = bool(resolve_when_post(post))
                        elif resolve_when is not None:
                            should_resolve = bool(resolve_when(text))
                    except Exception:
                        should_resolve = False
                if url is None and should_resolve:
                    try:
                        link = self.resolve_post_link(post)
                        if link:
                            post.url = link
                            m = re.search(r"/(?:posts|permalink)/(\d+)", link)
                            if m:
                                post.post_id = m.group(1)
                                post.key = f"{group_id}_{post.post_id}"
                            else:
                                m = re.search(r"/share/p/([A-Za-z0-9_-]+)", link)
                                if m:
                                    post.key = f"{group_id}_s{m.group(1)}"
                    except Exception:
                        pass
                if len(collected) >= max_posts:
                    break

            if reached:
                break
            if len(collected) >= max_posts:
                break
            if len(collected) <= last_collected:
                stall += 1
                if stall >= 5:
                    break
            else:
                stall = 0
            last_count = len(cards)
            last_collected = len(collected)
            try:
                page.mouse.move(400, 400)
                page.mouse.wheel(0, 3200)
                page.evaluate(
                    "() => { const f = document.querySelector('div[role=\"feed\"]');"
                    " if (f) { f.scrollTop = f.scrollHeight; }"
                    " else { window.scrollTo(0, document.body.scrollHeight); } }"
                )
            except Exception:
                pass
            time.sleep(1.8)

        posts = list(collected.values())[:max_posts]
        return posts

    def _expand_see_more(
        self,
        page,
        idx,
        text: str,
        expand_keywords: list[str] | None = None,
    ) -> str:
        if idx is None:
            return text
        if not any(marker in text for marker in ("查看更多", "顯示更多", "See more")):
            return text
        has_trade_hint = bool(
            re.search(r"售|賣|卖|價|价|[$＄]|元|NT|面交|自取|出清|割愛|拆售|合售", text, re.IGNORECASE)
        )
        low = text.lower()
        has_rule_hint = any(k.lower() in low for k in (expand_keywords or []) if k)
        if not has_trade_hint and not has_rule_hint:
            return text
        try:
            if page.evaluate(_EXPAND_CARD_JS, idx):
                time.sleep(0.6)
                expanded = page.evaluate(_CARD_TEXT_JS, idx)
                if expanded:
                    expanded = _INVISIBLE_RE.sub("", expanded).strip()
                    if len(expanded) > len(text):
                        return expanded
        except Exception:  # noqa: BLE001
            pass
        return text

    def read_post_text(self, url: str) -> str:
        """開啟單篇貼文網址，回傳貼文文字（測試器用）。"""
        assert self.page
        page = self.page
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        time.sleep(2.5)
        self._check_blocked(page)

        # 先展開「查看更多 / 顯示更多」，否則只會拿到截斷的內容
        for label in ("查看更多", "顯示更多", "See more"):
            try:
                btn = page.locator(f'div[role="article"] div[role="button"]:has-text("{label}")')
                for i in range(min(btn.count(), 3)):
                    btn.nth(i).click(timeout=1200)
                    page.wait_for_timeout(250)
            except Exception:
                continue

        # 以貼文本體為優先；標題只當最後手段（標題常常只有一行）
        texts = page.eval_on_selector_all(
            'div[role="article"], div[data-ad-preview="message"], div[data-ad-comet-preview="message"]',
            """els => els
                .filter(e => !(e.parentElement && e.parentElement.closest('div[role="article"]')))
                .map(e => (e.innerText || '').trim())
                .filter(t => t.length > 20)""",
        )
        if texts:
            return max(texts, key=len)[:6000]

        # 用貼文 ID 找到對應的連結，再往上找貼文容器，避免抓到整頁（含其他貼文與廣告）
        pid_match = re.search(r"/(?:posts|permalink)/(\d+)", url)
        if pid_match:
            post_text = page.evaluate(
                """(pid) => {
                    const a = Array.from(document.querySelectorAll('a[href*="' + pid + '"]'))[0];
                    if (!a) return '';
                    let el = a;
                    for (let i = 0; i < 10 && el.parentElement; i++) {
                        el = el.parentElement;
                        const t = (el.innerText || '').trim();
                        if (t.length >= 150 && t.length <= 3000) return t;
                    }
                    return '';
                }""",
                pid_match.group(1),
            )
            if post_text and len(post_text.strip()) > 50:
                return post_text.strip()[:6000]

        # 新版 Facebook 把貼文本體放在 role=main 之外，整頁 innerText 反而最完整
        try:
            body = page.inner_text("body")
        except Exception:
            body = ""
        body = "\n".join(
            line for line in body.splitlines()
            if line.strip() and line.strip() != "Facebook"
        )
        home = body.find("首頁")
        if home > 0:
            body = body[home + 2:]
        if len(body.strip()) > 150:
            return body.strip()[:6000]

        og = page.evaluate(
            "document.querySelector('meta[property=\"og:description\"]')?.content || ''"
        )
        if og and len(og.strip()) > 20:
            return og.strip()[:6000]

        og_title = page.evaluate(
            "document.querySelector('meta[property=\"og:title\"]')?.content || ''"
        )
        if og_title:
            text = _post_text_from_title(og_title)
            if len(text) > 20:
                return text[:6000]

        try:
            title_text = _post_text_from_title(page.title())
            if len(title_text) > 20:
                return title_text[:6000]
        except Exception:
            pass

        try:
            body = page.inner_text("body")
        except Exception:
            body = ""
        return body.strip()[:4000]

    def resolve_post_link(self, post: Post) -> str | None:
        """點開貼文的「回應」，讓網址變成 /permalink/<id>/，再組出直達連結。"""
        assert self.page
        page = self.page
        snip = _body_snippet(post.text)
        if len(snip) < 6:
            return None
        for _ in range(4):
            try:
                page.keyboard.press("Escape")
                time.sleep(0.2)
                idx = page.evaluate(_FIND_CARD_JS, snip)
                if idx is None:
                    page.evaluate("window.scrollBy(0, 3000)")
                    time.sleep(0.9)
                    continue
                if not page.evaluate(_CLICK_COMMENT_JS, idx):
                    page.evaluate("window.scrollBy(0, 1500)")
                    time.sleep(0.7)
                    continue
                time.sleep(1.6)
                url = page.url
                m = re.search(r"/groups/(\d+)/(?:permalink|posts)/(\d+)", url)
                if not m:
                    m2 = re.search(r"sale_post_id=(\d+)", url)
                    if m2:
                        gid = re.search(r"/groups/(\d+)/", url)
                        gid = gid.group(1) if gid else post.group_id
                        m = None
                        pid = m2.group(1)
                        link = f"https://www.facebook.com/groups/{gid}/posts/{pid}/"
                    else:
                        link = None
                else:
                    link = f"https://www.facebook.com/groups/{m.group(1)}/posts/{m.group(2)}/"
                page.keyboard.press("Escape")
                time.sleep(0.4)
                if "/permalink/" in page.url or "/posts/" in page.url:
                    page.go_back()
                    time.sleep(0.6)
                if link:
                    return link
            except Exception:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                time.sleep(0.4)
        return None

    def resolve_share_link(self, card_id: int) -> str | None:
        """點開該貼文的分享框，取出 /share/p/<code>/ 短連結。"""
        assert self.page
        page = self.page
        for _attempt in range(2):
            try:
                page.keyboard.press("Escape")
                time.sleep(0.25)
                if not page.evaluate(_CLICK_SHARE_JS, card_id):
                    continue
                page.wait_for_selector('div[role="dialog"]', timeout=6_000)
                for _ in range(8):
                    hrefs = page.eval_on_selector_all(
                        'div[role="dialog"] a[href]', "els => els.map(e => e.href)"
                    )
                    for href in hrefs:
                        decoded = unquote(href)
                        m = re.search(r"facebook\.com/share/p/([A-Za-z0-9_-]+)", decoded)
                        if m:
                            return f"https://www.facebook.com/share/p/{m.group(1)}/"
                    time.sleep(0.4)
            except Exception:
                pass
            finally:
                try:
                    page.keyboard.press("Escape")
                    time.sleep(0.35)
                except Exception:
                    pass
        return None

    # ------------------------------------------------------------ activity
    def group_last_post_days(self, group_url: str) -> tuple[float | None, str]:
        """讀社團第一則貼文的時間，回傳（幾天前, 原始文字）。"""
        assert self.page
        page = self.page
        try:
            page.goto(self._resolve_group_url(group_url), wait_until="domcontentloaded", timeout=60_000)
            time.sleep(1.5)
            self._check_blocked(page)
            page.wait_for_selector(
                f'div[role="button"][aria-label$="{_ACTION_LABEL_SUFFIX}"]', timeout=15_000
            )
        except Exception:
            return None, "讀不到貼文"

        raw = page.evaluate(
            """
            () => {
              const clean = s => (s || '')
                .replace(/[\\u200b-\\u200f\\u2060\\ufeff\\u0300-\\u036f\\u00ad]/g, '')
                .trim();
              const eb = [...document.querySelectorAll('div[role="button"][aria-label]')]
                .find(b => (b.getAttribute('aria-label') || '').endsWith('%s'));
              if (!eb) return '';
              let card = eb;
              for (let k = 0; k < 12 && card; k++) {
                if (card.querySelector('div[role="button"][aria-label*="分享"]')) break;
                card = card.parentElement;
              }
              if (!card) return '';
              for (const e of card.querySelectorAll('span,div,a')) {
                if (e.children.length > 1) continue;
                const t = clean(e.innerText || '');
                if (!t || t.length > 12) continue;
                if (/^(大約\\s*)?(\\d+\\s*(分鐘|小時|天|週|星期|個月|年)|昨天|剛剛)前?$/.test(t)) {
                  return t;
                }
              }
              return '';
            }
            """
            % _ACTION_LABEL_SUFFIX
        )
        return _relative_days(raw), raw or "無時間"

    # ------------------------------------------------------------ notifications
    def scan_notifications(self, monitored_ids: set[str], seen: set[str]) -> set[str]:
        """讀通知頁，回傳「有新貼文通知」的社團 ID 集合。

        只看 href 內含 /groups/<id> 或 ?group_id=<id> 的通知；
        加入審核、邀請、成員變動等通知會被忽略。
        """
        assert self.page
        page = self.page
        page.goto("https://www.facebook.com/notifications", wait_until="domcontentloaded", timeout=60_000)
        time.sleep(4)
        self._check_blocked(page)
        items = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                h: a.href,
                t: (a.innerText || '').trim()
            })).filter(x => x.t.length > 3)
            """
        )
        triggers: set[str] = set()
        for it in items:
            href = it.get("h") or ""
            parsed = urlparse(href)
            m = re.search(r"/groups/(\d+)", parsed.path)
            if not m:
                continue
            gid = m.group(1)
            if gid not in monitored_ids:
                continue
            qs = parse_qs(parsed.query)
            notif_t = (qs.get("notif_t") or [""])[0]
            if notif_t.startswith(_IGNORE_NOTIF_PREFIXES):
                continue
            key = (qs.get("notif_id") or [""])[0] or href
            if key in seen:
                continue
            seen.add(key)
            triggers.add(gid)
        return triggers

    # ------------------------------------------------------------ discovery
    def discover_groups(self, max_groups: int = 300) -> list[tuple[str, str]]:
        assert self.page
        page = self.page
        page.goto("https://www.facebook.com/groups/joins/", wait_until="domcontentloaded")
        time.sleep(2)
        self._check_blocked(page)
        found: dict[str, str] = {}
        for _ in range(12):
            items = page.evaluate(
                """
                () => {
                  const cardState = (a) => {
                    let e = a;
                    for (let i = 0; i < 6 && e; i++) {
                      const t = e.innerText || '';
                      if (t.includes('申請加入') || t.includes('回答問題') || t.includes('更新回覆')) return 'pending';
                      if (t.includes('查看社團') || t.includes('已加入')) return 'joined';
                      e = e.parentElement;
                    }
                    return 'unknown';
                  };
                  const heads = [...document.querySelectorAll('h1,h2,h3,[role="heading"]')];
                  const start = heads.find(e =>
                    (e.innerText || '').trim().startsWith('你加入的所有社團')
                  );
                  return Array.from(document.querySelectorAll('a[href*="/groups/"]'))
                    .filter(a => {
                      if (!start) return true;
                      return (start.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
                    })
                    .map(a => ({
                      href: a.href,
                      text: (a.innerText || '').trim(),
                      state: cardState(a),
                    }));
                }
                """
            )
            for it in items:
                if it.get("state") == "pending":
                    continue
                m = re.search(r"/groups/([^/?#]+)/?$", it["href"].split("?")[0])
                if not m:
                    continue
                ident = m.group(1)
                if ident in ("joins", "feed", "discover", "create", "your_groups"):
                    continue
                lines = (it["text"] or "").splitlines()
                name = lines[0].strip() if lines else ""
                if not name or len(name) > 80 or name.startswith("上次發文時間"):
                    continue
                found.setdefault(ident, name)
            if len(found) >= max_groups:
                break
            page.mouse.wheel(0, 2000)
            time.sleep(1.2)
        return [(name, f"https://www.facebook.com/groups/{ident}/") for ident, name in found.items()]
