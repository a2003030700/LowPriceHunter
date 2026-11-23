"""近 7 天價格調查：放寬價格區間、保留已售出，只依產品字過濾。"""

from __future__ import annotations

from .generic import Rule

SOLD_WORDS = {"已售出", "售出", "已成交", "已保留", "sold", "已下架"}


def build_survey_rule(rule: Rule) -> Rule:
    # 用「最低價的一半」當雜訊地板，濾掉 1、22、60 這種數量/型號數字
    floor = max(100, int(rule.min_price) // 2)
    return Rule(
        name=rule.name,
        keywords=list(rule.keywords),
        exclude=[w for w in rule.exclude if w not in SOLD_WORDS],
        require=list(rule.require),
        min_price=floor,
        max_price=10**9,
        price_near_keyword=True,
        near_distance=max(40, rule.near_distance),
        reject_multi_item=False,
        bundle_words=list(rule.bundle_words),
    )


def aggregate_daily(points: list[dict]) -> list[dict]:
    by_day: dict[str, list[int]] = {}
    for point in points:
        date = point.get("date")
        price = point.get("price")
        if not date or not price:
            continue
        by_day.setdefault(str(date), []).append(int(price))
    return [
        {
            "date": date,
            "count": len(values),
            "min": min(values),
            "avg": round(sum(values) / len(values)),
            "max": max(values),
        }
        for date, values in sorted(by_day.items())
    ]
