"""命令列介面。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from .config import Config, Group, ROOT, group_ident, load_config, save_groups
from .db import Store
from .notifier import send_deal, send_test_email
from .parser import analyze
from .scraper import AuthError, CheckpointError, FBScraper, LayoutError

LOG = logging.getLogger("ps5hunter")


# --------------------------------------------------------------------- utils
def setup_logging(cfg: Config, verbose: bool = True) -> None:
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    LOG.setLevel(logging.INFO)
    LOG.handlers.clear()
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(cfg.log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    LOG.addHandler(fh)
    if verbose and sys.stdout is not None:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        LOG.addHandler(sh)


class SingleInstance:
    """避免排程重疊執行（同一時間只允許一個掃描）。"""

    def __init__(self, path: Path):
        self.path = path
        self.fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            age = time.time() - self.path.stat().st_mtime
            if age < 3600:
                raise RuntimeError("已有另一個掃描正在執行，略過本次。")
            self.path.unlink(missing_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(self.fd, str(os.getpid()).encode())
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            self.path.unlink(missing_ok=True)


def _select_groups(cfg: Config, needle: str | None) -> list[Group]:
    if not needle:
        return cfg.groups
    return [g for g in cfg.groups if needle.lower() in (g.name + g.url).lower()]


def _require_mail(cfg: Config) -> None:
    if not cfg.mail_ready:
        raise SystemExit(
            "尚未設定 Gmail：請在 ps5-hunter/.env 填入 GMAIL_USER 與 GMAIL_APP_PASSWORD。"
        )


def _fmt_price(p: int | None) -> str:
    return f"NT${p:,.0f}" if p else "未標價"


# --------------------------------------------------------------------- check
def run_check(cfg: Config, args, only_groups: list[Group] | None = None) -> int:
    groups = only_groups if only_groups is not None else _select_groups(cfg, getattr(args, "group", None))
    if not groups:
        LOG.error("沒有可監控的社團，請先執行 `python -m ps5hunter discover --save` 或編輯 config.json。")
        return 3

    threshold = args.threshold or cfg.threshold
    dry_run = bool(args.dry_run)
    test_mode = bool(args.test)
    limit = args.limit or (1 if test_mode else 50)
    headless = cfg.headless and not args.headed
    max_posts = args.max_posts or cfg.max_posts_per_group

    store = Store(cfg.db_path)
    fresh = store.is_fresh()
    LOG.info("開始掃描 %d 個社團｜門檻 %s｜模式 %s", len(groups), _fmt_price(threshold),
             "測試" if test_mode else ("乾跑" if dry_run else "正式"))

    deals: list[tuple] = []          # (post, analysis)
    all_seen_posts = 0
    try:
        with FBScraper(cfg.profile_dir, channel=cfg.browser_channel,
                       headless=headless, timezone=cfg.timezone) as fb:
            if not fb.is_logged_in():
                raise AuthError("尚未登入 Facebook，請先執行 `python -m ps5hunter login`。")

            for idx, group in enumerate(groups, 1):
                LOG.info("[%d/%d] 讀取社團：%s", idx, len(groups), group.name)
                try:
                    posts = fb.scrape_group(
                        group.name,
                        group.url,
                        max_posts=max_posts,
                        resolve_when=lambda t, th=threshold: analyze(t, th).is_deal,
                    )
                except (LayoutError, CheckpointError) as exc:
                    LOG.warning("  跳過 %s：%s", group.name, exc)
                    continue

                LOG.info("  抓到 %d 篇貼文", len(posts))
                for post in posts:
                    already = store.seen(post.key)
                    if already and not test_mode:
                        continue
                    all_seen_posts += 1
                    result = analyze(post.text, threshold)
                    if result.is_deal:
                        if not post.url:
                            post.url = f"https://www.facebook.com/groups/{post.group_id}/"
                        LOG.info("  ✅ 命中：%s｜%s", _fmt_price(result.price), post.url)
                        deals.append((post, result))
                    elif result.is_ps5 and result.price:
                        LOG.info("  ·  PS5 但 %s：%s", _fmt_price(result.price), post.short_text[:40])
                    if not test_mode and not dry_run:
                        store.remember(post.key, post.group_name, post.url or group.url,
                                       result.price, post.text, notified=False)
                if len(deals) >= limit:
                    break
    finally:
        store.close()

    if not deals:
        LOG.info("本次沒有符合條件的貼文（掃描 %d 篇新貼文）。", all_seen_posts)
        return 0

    # 優先處理有「直接貼文連結」的，避免測試信只連到社團首頁
    def _direct(url: str | None) -> bool:
        return bool(url) and any(x in url for x in ("/posts/", "/commerce/", "/share/p/"))

    deals.sort(key=lambda pa: 0 if _direct(pa[0].url) else 1)

    # 第一次執行：只建立基準線，不轟炸信箱
    if fresh and not cfg.notify_on_first_run and not test_mode and not dry_run:
        LOG.info("第一次執行，已記錄 %d 篇為基準線，不發送通知。下次起才會通知新貼文。", len(deals))
        store = Store(cfg.db_path)
        for post, result in deals:
            store.remember(post.key, post.group_name, post.url or "", result.price,
                           post.text, notified=True)
        store.set_baseline()
        store.close()
        return 0

    if dry_run:
        print(json.dumps(
            [{"group": p.group_name, "price": a.price, "url": p.url,
              "text": p.short_text[:200]} for p, a in deals[:limit]],
            ensure_ascii=False, indent=2))
        return 0

    _require_mail(cfg)
    sent = 0
    for post, result in deals[:limit]:
        try:
            send_deal(post, result, threshold, cfg.gmail_user,
                      cfg.gmail_app_password, cfg.mail_to)
        except Exception as exc:  # noqa: BLE001
            LOG.error("寄信失敗：%s（下次會重試）", exc)
            continue
        sent += 1
        LOG.info("  📧 已寄出通知：%s %s", _fmt_price(result.price), post.url)
        store = Store(cfg.db_path)
        store.remember(post.key, post.group_name, post.url, result.price,
                       post.text, notified=True)
        store.close()

    if sent:
        store = Store(cfg.db_path)
        if store.is_fresh():
            store.set_baseline()
        store.close()
    LOG.info("完成：寄出 %d 封通知。", sent)
    return 0 if sent or not deals else 4


# --------------------------------------------------------------------- commands
def cmd_login(cfg: Config, args) -> int:
    with FBScraper(cfg.profile_dir, channel=cfg.browser_channel,
                   headless=False, timezone=cfg.timezone) as fb:
        if fb.is_logged_in():
            print("✅ 已經是登入狀態，不需要重新登入。")
            return 0
        print("已開啟瀏覽器，請在視窗中登入 Facebook（最多等 10 分鐘）。")
        fb.wait_for_login(timeout_seconds=600)
        print("✅ 登入成功， session 已存到：", cfg.profile_dir)
    return 0


def cmd_discover(cfg: Config, args) -> int:
    with FBScraper(cfg.profile_dir, channel=cfg.browser_channel,
                   headless=not args.headed, timezone=cfg.timezone) as fb:
        if not fb.is_logged_in():
            raise AuthError("尚未登入 Facebook，請先執行 `python -m ps5hunter login`。")
        found = fb.discover_groups()
    if not found:
        print("找不到已加入的社團。")
        return 1
    groups = [Group(name=n, url=u) for n, u in found]
    print(f"找到 {len(groups)} 個已加入的社團：\n")
    for i, g in enumerate(groups, 1):
        print(f"{i:3d}. {g.name}\n     {g.url}")
    if args.save:
        picked = groups
        if args.filter:
            needle = args.filter.lower()
            picked = [g for g in groups if needle in g.name.lower()]
            print(f"\n符合「{args.filter}」的社團 {len(picked)} 個，已寫入 config.json。")
        save_groups(picked)
        print(f"✅ 已寫入 {ROOT / 'config.json'}")
    return 0


def cmd_check(cfg: Config, args) -> int:
    with SingleInstance(cfg.db_path.parent / "scan.lock"):
        return run_check(cfg, args)


def cmd_monitor(cfg: Config, args) -> int:
    interval = args.interval or cfg.interval_seconds
    LOG.info("常駐監控啟動，間隔 %d 秒。按 Ctrl+C 停止。", interval)
    errors = 0
    while True:
        try:
            with SingleInstance(cfg.db_path.parent / "scan.lock"):
                run_check(cfg, args)
            errors = 0
        except KeyboardInterrupt:
            LOG.info("已停止監控。")
            return 0
        except (AuthError, CheckpointError) as exc:
            LOG.error("需要人工處理：%s", exc)
            return 2
        except Exception as exc:  # noqa: BLE001
            errors += 1
            LOG.error("掃描失敗（第 %d 次）：%s", errors, exc)
        wait = interval + random.randint(0, max(20, interval // 5))
        if errors:
            wait = min(wait * (2 ** min(errors, 4)), 1800)
        LOG.info("下次掃描：%s（約 %d 秒後）",
                 datetime.now().strftime("%H:%M:%S"), wait)
        try:
            time.sleep(wait)
        except KeyboardInterrupt:
            return 0


def cmd_test_email(cfg: Config, args) -> int:
    _require_mail(cfg)
    send_test_email(cfg.gmail_user, cfg.gmail_app_password, cfg.mail_to)
    print(f"✅ 測試信已寄到 {cfg.mail_to}")
    return 0


def cmd_prune(cfg: Config, args) -> int:
    """移除超過 N 天沒有新貼文的社團。"""
    keep: list[Group] = []
    removed: list[tuple[Group, str]] = []
    with FBScraper(cfg.profile_dir, channel=cfg.browser_channel,
                   headless=not args.headed, timezone=cfg.timezone) as fb:
        if not fb.is_logged_in():
            raise AuthError("尚未登入 Facebook，請先執行 `python -m ps5hunter login`。")
        total = len(cfg.groups)
        for idx, group in enumerate(cfg.groups, 1):
            days, raw = fb.group_last_post_days(group.url)
            if days is None:
                keep.append(group)
                print(f"[{idx}/{total}] ? 保留（{raw}）：{group.name}")
            elif days > args.days:
                removed.append((group, raw))
                print(f"[{idx}/{total}] ✂ 排除（{raw}）：{group.name}")
            else:
                keep.append(group)
                print(f"[{idx}/{total}] ✓ 保留（{raw}）：{group.name}")

    if removed and not args.dry_run:
        save_groups(keep)
        print(f"\n已從 config.json 移除 {len(removed)} 個社團，剩 {len(keep)} 個。")
    elif removed:
        print(f"\n[乾跑] 會移除 {len(removed)} 個社團，剩 {len(keep)} 個。")
    else:
        print(f"\n沒有超過 {args.days} 天沒新貼文的社團。")
    return 0


def _watch_args() -> argparse.Namespace:
    return argparse.Namespace(
        threshold=None, dry_run=False, test=False, limit=None,
        group=None, headed=False, max_posts=None,
    )


def _load_seen_notifications(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("keys", []))
    except Exception:
        return set()


def _save_seen_notifications(path: Path, seen: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = sorted(seen)[-1500:]
    path.write_text(json.dumps({"keys": keys}, ensure_ascii=False), encoding="utf-8")


def cmd_watch(cfg: Config, args) -> int:
    """小帳專用：看通知頁決定要不要掃描，找不到通知時靠保底全掃。"""
    interval = args.interval or cfg.notification_interval_seconds
    full_interval = cfg.full_scan_interval_seconds if args.full_interval is None else args.full_interval
    seen_path = cfg.db_path.parent / "notif_seen.json"
    seen = _load_seen_notifications(seen_path)
    monitored = {group_ident(g.url) for g in cfg.groups}
    LOG.info(
        "通知監控啟動：每 %d 秒看通知｜保底全掃 %s 秒｜%d 個社團",
        interval, full_interval or "停用", len(cfg.groups),
    )

    fb: FBScraper | None = None

    def open_browser() -> None:
        nonlocal fb
        fb = FBScraper(cfg.profile_dir, channel=cfg.browser_channel,
                       headless=cfg.headless, timezone=cfg.timezone)
        fb.__enter__()
        if not fb.is_logged_in():
            raise AuthError("尚未登入 Facebook，請先執行 `python -m ps5hunter login`。")

    def close_browser() -> None:
        nonlocal fb
        if fb is not None:
            try:
                fb.__exit__(None, None, None)
            except Exception:
                pass
            fb = None

    last_full = 0.0
    try:
        open_browser()
        while True:
            try:
                assert fb is not None
                triggers = fb.scan_notifications(monitored, seen)
                _save_seen_notifications(seen_path, seen)

                if triggers:
                    targets = [g for g in cfg.groups if group_ident(g.url) in triggers]
                    LOG.info("通知觸發 %d 個社團：%s", len(targets), "、".join(g.name for g in targets))
                    close_browser()
                    run_check(cfg, _watch_args(), only_groups=targets)
                    open_browser()

                if full_interval and time.time() - last_full >= full_interval:
                    LOG.info("保底全量掃描 %d 個社團…", len(cfg.groups))
                    close_browser()
                    run_check(cfg, _watch_args(), only_groups=cfg.groups)
                    open_browser()
                    last_full = time.time()
            except (AuthError, CheckpointError):
                raise
            except Exception as exc:  # noqa: BLE001
                LOG.error("通知監控錯誤：%s", exc)
                close_browser()
                time.sleep(max(10, interval // 2))
                try:
                    open_browser()
                except Exception as exc2:  # noqa: BLE001
                    LOG.error("重新開啟瀏覽器失敗：%s", exc2)
                    return 2

            if args.once:
                return 0
            time.sleep(interval + random.randint(0, max(5, interval // 5)))
    except (AuthError, CheckpointError) as exc:
        LOG.error("需要人工處理：%s", exc)
        return 2
    finally:
        close_browser()


def cmd_doctor(cfg: Config, args) -> int:
    print(f"Python      : {sys.version.split()[0]}")
    try:
        import playwright  # noqa: F401
        print("Playwright  : 已安裝")
    except ImportError:
        print("Playwright  : ❌ 未安裝")
        return 1
    print(f"瀏覽器頻道  : {cfg.browser_channel}")
    print(f"社團數量    : {len(cfg.groups)}")
    print(f"門檻        : {cfg.threshold:,} 元")
    print(f"資料庫      : {cfg.db_path}")
    print(f"瀏覽器設定檔: {cfg.profile_dir}")
    print(f"Gmail 帳號  : {cfg.gmail_user or '❌ 未設定'}")
    print(f"通知對象    : {cfg.mail_to or '❌ 未設定'}")
    with FBScraper(cfg.profile_dir, channel=cfg.browser_channel,
                   headless=True, timezone=cfg.timezone) as fb:
        print(f"FB 登入狀態 : {'✅ 已登入' if fb.is_logged_in() else '❌ 未登入'}")
    return 0


# --------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ps5hunter", description="PS5 社團低價監控")
    p.add_argument("--config", type=Path, default=None)
    p.add_argument("-v", "--verbose", action="store_true", default=True)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="開啟瀏覽器登入 Facebook（只需一次）")

    d = sub.add_parser("discover", help="列出你已加入的 Facebook 社團")
    d.add_argument("--save", action="store_true", help="寫入 config.json")
    d.add_argument("--filter", type=str, default=None, help="只儲存名稱含此字串的社團")
    d.add_argument("--headed", action="store_true", help="顯示瀏覽器視窗")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--threshold", type=int, default=None, help="價格門檻（元）")
    common.add_argument("--dry-run", action="store_true", help="只印出結果，不寄信")
    common.add_argument("--test", action="store_true", help="測試模式：忽略去重，找到就寄")
    common.add_argument("--limit", type=int, default=None, help="最多寄幾封")
    common.add_argument("--group", type=str, default=None, help="只掃描名稱含此字串的社團")
    common.add_argument("--headed", action="store_true", help="顯示瀏覽器視窗")
    common.add_argument("--max-posts", type=int, default=None)

    sub.add_parser("check", parents=[common], help="掃描一次")
    sub.add_parser("monitor", parents=[common], help="常駐定時掃描").add_argument(
        "--interval", type=int, default=None, help="秒")

    sub.add_parser("test-email", help="寄一封測試信確認 Gmail 設定")

    w = sub.add_parser("watch", parents=[common], help="通知觸發監控（小帳用）")
    w.add_argument("--interval", type=int, default=None, help="看通知的間隔秒數")
    w.add_argument("--full-interval", type=int, default=None, help="保底全量掃描間隔秒數，0=停用")
    w.add_argument("--once", action="store_true", help="只跑一輪就結束（測試用）")

    pr = sub.add_parser("prune", help="移除超過 N 天沒有新貼文的社團")
    pr.add_argument("--days", type=int, default=7, help="幾天沒貼文就排除（預設 7）")
    pr.add_argument("--dry-run", action="store_true", help="只列出，不修改 config.json")
    pr.add_argument("--headed", action="store_true", help="顯示瀏覽器視窗")

    sub.add_parser("doctor", help="檢查環境與登入狀態")
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg, verbose=args.verbose)

    if args.command == "discover" and not getattr(args, "threshold", None):
        pass

    handlers = {
        "login": cmd_login,
        "discover": cmd_discover,
        "check": cmd_check,
        "monitor": cmd_monitor,
        "test-email": cmd_test_email,
        "watch": cmd_watch,
        "prune": cmd_prune,
        "doctor": cmd_doctor,
    }
    try:
        return handlers[args.command](cfg, args)
    except AuthError as exc:
        LOG.error("%s", exc)
        print(f"\n👉 請先執行：python -m ps5hunter login")
        return 2
    except CheckpointError as exc:
        LOG.error("%s", exc)
        return 2
    except RuntimeError as exc:
        LOG.warning("%s", exc)
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
