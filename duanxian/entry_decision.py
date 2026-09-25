"""Deterministic review-time environment gate; never an order trigger."""

from __future__ import annotations

from datetime import date as iso_date
from math import isfinite


def _record(value) -> dict:
    return value if isinstance(value, dict) else {}


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) else None


def evaluate_entry(env: dict) -> dict:
    """Use only this saved review. Missing data cannot grant eligibility."""
    metrics = _record(env.get("emotion_metrics"))
    facts = _record(env.get("market_facts"))
    date = env.get("target_date") or env.get("trade_date")
    issues = []
    try:
        iso_date.fromisoformat(date)
    except (TypeError, ValueError):
        issues.append("复盘日期缺失或无效")
    for source in (metrics, facts):
        if source.get("date") and source["date"] != date:
            issues.append("指标日期与复盘日期不一致")
    # Review warnings also cover unrelated analyst inputs such as the theme
    # narrative and dragon-tiger list.  The gate validates every core numeric
    # source below, so a generic warning must not erase otherwise complete
    # environment evidence.

    promotion = _record(metrics.get("promotion"))
    tier = _record(_record(promotion.get("tiers")).get("1进2"))
    # Correlated observations are shown separately, not added into a probability.
    specs = [
        ("money", "昨日涨停股收益中位数", _record(metrics.get("money_effect")), "median", "pct", 10, 1, -1, -2),
        ("premium", "昨日连板股收益中位数", _record(metrics.get("consec_premium")), "median", "pct", 3, 1, -1, -3),
        ("promotion", "1进2晋级率", {**promotion, **tier, "sample": tier.get("base")},
         "rate", "rate", 10, .25, .10, None),
        ("broken", "炸板未回封率", _record(facts.get("seal_quality")), "broken_rate", "rate", 0, .25, .40, .50),
        ("loss", "昨日涨停股跌幅至少5%占比", _record(facts.get("loss_effect")), "deep_loss_5_rate", "rate", 10, .10, .20, .30),
    ]
    signals = []
    vetoes = []
    for key, label, source, field, unit, minimum, bull, bear, veto in specs:
        value = _number(source.get(field))
        sample = _number(source.get("sample"))
        coverage = _number(source.get("coverage_rate"))
        reason = ""
        if source.get("available") is not True:
            reason = "数据不可用"
        elif source.get("date") and source["date"] != date:
            reason = "指标日期与复盘日期不一致"
        elif value is None or (unit == "rate" and not 0 <= value <= 1):
            reason = "读数缺失或无效"
        elif minimum and (sample is None or sample < minimum):
            reason = f"有效样本不足{minimum}只"
        elif (source.get("partial") or
              ("coverage_rate" in source and (coverage is None or not .9 <= coverage <= 1))):
            reason = "样本覆盖不足90%"
        lower_is_better = key in ("broken", "loss")
        state = "missing" if reason else "neutral"
        if not reason:
            if (value <= bull if lower_is_better else value >= bull):
                state = "bullish"
            elif (value >= bear if lower_is_better else value <= bear):
                state = "bearish"
            if veto is not None and (value >= veto if lower_is_better else value <= veto):
                vetoes.append(key)
        signals.append({"key": key, "label": label, "value": value, "unit": unit,
                        "state": state, "reason": reason, "sample": sample,
                        "bull_threshold": bull, "bear_threshold": bear,
                        "veto_threshold": veto, "lower_is_better": lower_is_better})

    bullish = sum(s["state"] == "bullish" for s in signals)
    bearish = sum(s["state"] == "bearish" for s in signals)
    missing = [s["key"] for s in signals if s["state"] == "missing"]
    states = {s["key"]: s["state"] for s in signals}
    if vetoes:
        status, label, reason = "blocked", "暂不出手", "触发风险否决，正向读数不能抵消严重亏钱或炸板风险。"
    elif missing or issues:
        status, label, reason = "insufficient", "数据不足", "核心数据或日期校验未通过，不能授予备选资格。"
    elif bearish >= 2:
        status, label, reason = "blocked", "暂不出手", "至少两项读数偏空，接力环境承压。"
    elif bullish >= 4 and bearish == 0 and all(states[k] == "bullish" for k in ("money", "promotion", "broken")):
        status, label, reason = "candidate", "可纳入备选", "赚钱反馈、晋级和封板环境通过初筛；个股与盘中买点仍未确认。"
    elif bullish >= 2:
        status, label, reason = "observe", "仅观察", "存在正向反馈，但尚未同时满足备选条件。"
    else:
        status, label, reason = "wait", "等待确认", "有效读数未形成足够的正向支持。"
    return {"version": "environment-v1", "scope": "review_only", "date": date,
            "status": status, "label": label, "reason": reason,
            "candidate_eligible": status == "candidate", "bullish_count": bullish,
            "bearish_count": bearish, "signals": signals, "vetoes": vetoes,
            "missing": missing, "issues": issues,
            "validation": "启发式阈值，尚未回测；多空读数相关，数量不代表胜率。",
            "pending_checks": ["次日实时情绪是否延续", "题材及个股承接、公告风险",
                               "盘口封单与成交可得性", "个人仓位、退出规则与风险限额"]}
