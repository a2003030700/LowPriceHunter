"""透過 Gmail SMTP 寄送 HTML 通知信。"""

from __future__ import annotations

import html
import json
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate

from .parser import Analysis
from .scraper import Post


class NotificationError(RuntimeError):
    pass


def _post_json(url: str, payload: dict, timeout: int = 20) -> None:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Discord / Telegram 前面的 Cloudflare 會擋掉 Python-urllib 預設 UA（403）
            "User-Agent": "LowPriceHunter/1.4.2 (+https://github.com/a2003030700/LowPriceHunter)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "ignore").strip()[:200]
        except Exception:
            detail = ""
        raise NotificationError(f"HTTP {exc.code}" + (f"：{detail}" if detail else "")) from None
    except Exception:
        raise NotificationError("連線失敗") from None


def send_telegram(token: str, chat_id: str, text: str) -> None:
    if not token or not chat_id:
        raise NotificationError("Telegram 尚未設定")
    _post_json(
        f"https://api.telegram.org/bot{token}/sendMessage",
        {"chat_id": chat_id, "text": text[:3800], "disable_web_page_preview": False},
    )


def build_discord_embed(
    rule_name: str,
    price: int,
    group_name: str,
    post_url: str,
    snippet: str,
    price_high: int | None = None,
    sources: list[str] | None = None,
    risk_words: list[str] | None = None,
    is_split_sale: bool = False,
    image_url: str = "",
    author: str = "",
) -> dict:
    """建立 Discord Embed 卡片（含貼文縮圖、可點擊直達連結、價格、社團、賣家與風險標記）。"""
    price_text = _fmt_price_range(price, price_high)
    sources = sources or [group_name]
    split_tag = "（拆售價）" if is_split_sale else ""
    warn_prefix = "⚠️ " if risk_words else "🔔 "
    fields = [
        {"name": "價格", "value": f"**{price_text}**{split_tag}", "inline": True},
        {
            "name": "社團",
            "value": ("、".join(sources) + f"（共 {len(sources)} 個）") if len(sources) > 1 else group_name,
            "inline": True,
        },
    ]
    if author:
        fields.append({"name": "賣家", "value": author[:60], "inline": True})
    if risk_words:
        fields.append({"name": "⚠️ 高風險詞", "value": "、".join(risk_words), "inline": False})
    if post_url:
        fields.append({"name": "快速開啟貼文", "value": f"[點擊直接前往貼文 →]({post_url})", "inline": False})

    embed: dict = {
        "title": f"{warn_prefix}{rule_name}｜{price_text}{split_tag}",
        "description": (snippet or "")[:600],
        "color": 0xF59E0B if risk_words else 0x10B981,
        "fields": fields,
        "footer": {"text": "Low Price Hunter v1.4 即時推播"},
    }
    if post_url and post_url.startswith(("http://", "https://")):
        embed["url"] = post_url
    if image_url and image_url.startswith(("http://", "https://")):
        embed["image"] = {"url": image_url}
    return embed


def build_discord_alert_embed(title: str, message: str, level: str = "error") -> dict:
    """建立系統警報或每日健康回報用的 Discord Embed 卡片。"""
    color = 0xDC2626 if level == "error" else (0xD97706 if level == "warn" else 0x2563EB)
    return {
        "title": title[:240],
        "description": (message or "")[:1800],
        "color": color,
        "footer": {"text": "Low Price Hunter 系統監控回報"},
    }


def send_discord(webhook_url: str, text: str, embed: dict | None = None) -> None:
    if not webhook_url:
        raise NotificationError("Discord 尚未設定")
    payload: dict = {"content": (text or "")[:1900]}
    if embed:
        payload["embeds"] = [embed]
    _post_json(webhook_url, payload)


def build_text_notification(
    rule_name: str,
    price: int,
    group_name: str,
    post_url: str,
    snippet: str,
    price_high: int | None = None,
    sources: list[str] | None = None,
    risk_words: list[str] | None = None,
) -> str:
    price_text = _fmt_price_range(price, price_high)
    sources = sources or [group_name]
    lines = [f"🔔 {rule_name} 符合條件：{price_text}"]
    if len(sources) > 1:
        lines.append(f"社團：{'、'.join(sources)}（共 {len(sources)} 個）")
    else:
        lines.append(f"社團：{group_name}")
    if risk_words:
        lines.append(f"⚠️ 高風險：{'、'.join(risk_words)}")
    lines.append(f"連結：{post_url}")
    if snippet:
        lines.append("")
        lines.append(snippet[:400])
    return "\n".join(lines)


def _fmt_price(amount: int | None) -> str:
    if amount is None:
        return "價格未標示"
    return f"NT$ {amount:,.0f}"


def build_message(post: Post, analysis: Analysis, threshold: int, sender: str, to: str) -> EmailMessage:
    subject = (
        f"🔥 PS5 甜甜價 {_fmt_price(analysis.price)}｜{post.group_name}"
        if analysis.price
        else f"🔥 PS5 貼文通知｜{post.group_name}"
    )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)

    snippet = html.escape(post.short_text[:900])
    link = html.escape(post.url, quote=True)
    kw = html.escape(analysis.matched_keyword or "PS5")

    text_body = (
        f"PS5 低價通知\n\n"
        f"社團：{post.group_name}\n"
        f"價格：{_fmt_price(analysis.price)}（門檻 {threshold:,}）\n"
        f"貼文：{post.url}\n\n"
        f"{post.short_text[:900]}\n"
    )
    msg.set_content(text_body)

    html_body = f"""\
<!doctype html>
<html lang="zh-Hant">
<body style="margin:0;padding:24px;background:#f4f5f7;font-family:-apple-system,'Segoe UI','Microsoft JhengHei',sans-serif;">
  <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;border:1px solid #e3e6ea;">
    <div style="background:#0f172a;color:#fff;padding:20px 24px;">
      <div style="font-size:13px;opacity:.75;letter-spacing:.08em;">PS5 社團低價監控</div>
      <div style="font-size:26px;font-weight:700;margin-top:6px;">{_fmt_price(analysis.price)}</div>
      <div style="font-size:13px;opacity:.8;margin-top:4px;">門檻 {threshold:,} 元｜關鍵字 {kw}</div>
    </div>
    <div style="padding:22px 24px;">
      <p style="margin:0 0 6px;font-size:13px;color:#6b7280;">社團</p>
      <p style="margin:0 0 18px;font-size:16px;font-weight:600;color:#111827;">{html.escape(post.group_name)}</p>
      <p style="margin:0 0 6px;font-size:13px;color:#6b7280;">貼文內容</p>
      <div style="background:#f9fafb;border:1px solid #eef0f3;border-radius:10px;padding:14px;font-size:14px;line-height:1.65;color:#1f2937;white-space:pre-wrap;">{snippet}</div>
      <a href="{link}" style="display:block;text-align:center;margin-top:22px;background:#2563eb;color:#fff;text-decoration:none;padding:14px;border-radius:10px;font-weight:700;font-size:15px;">立即開啟這篇貼文 →</a>
      <p style="margin:14px 0 0;font-size:12px;color:#9ca3af;word-break:break-all;">{link}</p>
    </div>
  </div>
</body>
</html>
"""
    msg.add_alternative(html_body, subtype="html")
    return msg


def send_gmail(msg: EmailMessage, user: str, app_password: str, timeout: int = 30) -> None:
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=timeout) as smtp:
        smtp.login(user, app_password.replace(" ", ""))
        smtp.send_message(msg)


def send_deal(
    post: Post,
    analysis: Analysis,
    threshold: int,
    gmail_user: str,
    app_password: str,
    to: str,
) -> None:
    msg = build_message(post, analysis, threshold, gmail_user, to)
    send_gmail(msg, gmail_user, app_password)


def send_test_email(gmail_user: str, app_password: str, to: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = "✅ PS5 Hunter 測試信"
    msg["From"] = gmail_user
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg.set_content("如果你收到這封信，代表 Gmail 通知設定成功。")
    msg.add_alternative(
        "<p style=\"font-family:sans-serif\">如果你收到這封信，代表 <b>Gmail 通知設定成功</b> ✅</p>",
        subtype="html",
    )
    send_gmail(msg, gmail_user, app_password)


def build_digest_message(items: list[dict], sender: str, to: str, day: str) -> EmailMessage:
    """每日摘要：一次列出多筆符合條件的貼文。"""
    msg = EmailMessage()
    msg["Subject"] = f"📬 Low Price Hunter 每日摘要（{day}，{len(items)} 筆）"
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)

    lines = [f"{day} 共 {len(items)} 筆符合條件\n"]
    current = None
    for i, it in enumerate(items, 1):
        rule = it.get("rule") or "其他"
        if rule != current:
            lines.append(f"\n【{rule}】")
            current = rule
        lines.append(
            f"{i}. NT$ {it['price']:,.0f}｜{it['group']}\n   {it['url']}\n   {it['text'][:120]}\n"
        )
    msg.set_content("\n".join(lines))

    rows = []
    current = None
    for it in items:
        rule = it.get("rule") or "其他"
        if rule != current:
            rows.append(
                f'<div style="margin:18px 0 4px;font-size:13px;font-weight:700;color:#2563eb;">{html.escape(rule)}</div>'
            )
            current = rule
        rows.append(
            f"""<div style="padding:14px 0;border-bottom:1px solid #eef0f3;">
  <div style="font-size:18px;font-weight:700;color:#111827;">NT$ {it['price']:,.0f}</div>
  <div style="font-size:13px;color:#6b7280;margin:2px 0 6px;">{html.escape(it['group'])}</div>
  <div style="font-size:13px;line-height:1.5;color:#374151;">{html.escape(it['text'][:160])}</div>
  <a href="{html.escape(it['url'], quote=True)}" style="display:inline-block;margin-top:8px;color:#2563eb;font-size:13px;">開啟貼文 →</a>
</div>"""
        )

    html_body = f"""\
<!doctype html>
<html lang="zh-Hant">
<body style="margin:0;padding:24px;background:#f4f5f7;font-family:-apple-system,'Segoe UI','Microsoft JhengHei',sans-serif;">
  <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;border:1px solid #e3e6ea;">
    <div style="background:#0f172a;color:#fff;padding:20px 24px;">
      <div style="font-size:13px;opacity:.75;letter-spacing:.08em;">LOW PRICE HUNTER</div>
      <div style="font-size:24px;font-weight:700;margin-top:6px;">每日摘要</div>
      <div style="font-size:13px;opacity:.8;margin-top:4px;">{day}｜{len(items)} 筆符合條件</div>
    </div>
    <div style="padding:8px 24px 20px;">{''.join(rows)}</div>
  </div>
</body>
</html>
"""
    msg.add_alternative(html_body, subtype="html")
    return msg


def _fmt_price_range(low: int, high: int | None) -> str:
    if high and high != low:
        return f"NT$ {low:,.0f}–{high:,.0f}"
    return f"NT$ {low:,.0f}"


def build_generic_message(
    rule_name: str,
    price: int,
    group_name: str,
    post_url: str,
    snippet: str,
    sender: str,
    to: str,
    price_high: int | None = None,
    sources: list[str] | None = None,
    risk_words: list[str] | None = None,
) -> EmailMessage:
    price_text = _fmt_price_range(price, price_high)
    sources = sources or [group_name]
    group_text = "、".join(sources)
    source_count = len(sources)
    risk_text = "、".join(risk_words or [])
    subject = (
        f"{'⚠️ ' if risk_text else ''}🔔 {rule_name} 符合條件：{price_text}"
        f"｜{source_count} 個社團" if source_count > 1
        else f"{'⚠️ ' if risk_text else ''}🔔 {rule_name} 符合條件：{price_text}｜{group_name}"
    )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)

    text = (
        f"{rule_name} 符合條件\n\n"
        f"價格：{price_text}\n"
        f"社團：{group_text}" + (f"（共 {source_count} 個）" if source_count > 1 else "") + "\n"
        + (f"⚠️ 高風險詞：{risk_text}\n" if risk_text else "")
        + f"連結：{post_url}\n\n"
        + f"{snippet[:900]}\n"
    )
    msg.set_content(text)

    html_body = f"""\
<!doctype html>
<html lang="zh-Hant">
<body style="margin:0;padding:24px;background:#f4f5f7;font-family:-apple-system,'Segoe UI','Microsoft JhengHei',sans-serif;">
  <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;border:1px solid #e3e6ea;">
    <div style="background:#0f172a;color:#fff;padding:20px 24px;">
      <div style="font-size:13px;opacity:.75;letter-spacing:.08em;">社團好物監控</div>
      <div style="font-size:26px;font-weight:700;margin-top:6px;">{html.escape(rule_name)}　{price_text}</div>
      <div style="font-size:13px;opacity:.8;margin-top:4px;">{html.escape(group_text)}{f"（共 {source_count} 個）" if source_count > 1 else ""}</div>
    </div>
    {f'<div style="margin:0;padding:10px 24px;background:#fef2f2;color:#991b1b;font-size:13px;border-bottom:1px solid #fecaca;">⚠️ 高風險詞：{html.escape(risk_text)}</div>' if risk_text else ''}
    <div style="padding:22px 24px;">
      <p style="margin:0 0 6px;font-size:13px;color:#6b7280;">貼文內容</p>
      <div style="background:#f9fafb;border:1px solid #eef0f3;border-radius:10px;padding:14px;font-size:14px;line-height:1.65;color:#1f2937;white-space:pre-wrap;">{html.escape(snippet[:900])}</div>
      <a href="{html.escape(post_url, quote=True)}" style="display:block;text-align:center;margin-top:22px;background:#2563eb;color:#fff;text-decoration:none;padding:14px;border-radius:10px;font-weight:700;font-size:15px;">立即開啟這篇貼文 →</a>
      <p style="margin:14px 0 0;font-size:12px;color:#9ca3af;word-break:break-all;">{html.escape(post_url)}</p>
    </div>
  </div>
</body>
</html>
"""
    msg.add_alternative(html_body, subtype="html")
    return msg

