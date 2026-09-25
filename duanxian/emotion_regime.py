"""Deterministic short-term emotion phase and retreat-risk evaluation.

The five phases are an operational heuristic for this project, not a trading
signal.  The evaluator consumes already-saved facts only; missing values never
become zero and never create a risk alert.
"""

from __future__ import annotations

from math import isfinite
from typing import Optional


VERSION = "emotion-regime-v1"
PHASES = ("冰点", "修复", "发酵", "亢奋", "退潮")
CORE_GROUPS = frozenset(("feedback", "continuation", "seal", "loss"))


def _record(value) -> dict:
    return value if isinstance(value, dict) else {}


def _number(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if isfinite(value) else None


def _rate(value) -> Optional[float]:
    value = _number(value)
    return value if value is not None and 0 <= value <= 1 else None


def _metric_value(source: dict, field: str, *, minimum_sample: int = 0,
                  rate: bool = False) -> Optional[float]:
    """Read a settled metric only when its explicit quality flags are sound."""
    if source.get("available") is not True or source.get("partial") is True:
        return None
    coverage = _rate(source.get("coverage_rate")) if "coverage_rate" in source else None
    if "coverage_rate" in source and (coverage is None or coverage < .90):
        return None
    sample = _number(source.get("sample"))
    if sample is not None and sample < minimum_sample:
        return None
    return _rate(source.get(field)) if rate else _number(source.get(field))


def _fmt(value: Optional[float], kind: str = "number") -> str:
    if value is None:
        return "—"
    if kind == "rate":
        return f"{value:.0%}"
    if kind == "pct":
        return f"{value:+.2f}%"
    return f"{value:g}"


def _trend_values(facts: dict, key: str) -> list[float]:
    trend = _record(facts.get("trend"))
    for metric in trend.get("metrics") or []:
        if isinstance(metric, dict) and metric.get("key") == key:
            return [n for value in (metric.get("values") or []) if (n := _number(value)) is not None]
    return []


def _signal(key: str, label: str, state: str, detail: str,
            *, severe: bool = False, value: Optional[float] = None) -> dict:
    return {"key": key, "label": label, "state": state, "severe": severe,
            "detail": detail, "value": value}


def _settled_signals(env: dict) -> list[dict]:
    metrics = _record(env.get("emotion_metrics"))
    facts = _record(env.get("market_facts"))

    money = _record(metrics.get("money_effect"))
    premium = _record(metrics.get("consec_premium"))
    money_v = _metric_value(money, "median", minimum_sample=10)
    premium_v = _metric_value(premium, "median", minimum_sample=3)
    if money_v is None and premium_v is None:
        feedback = _signal("feedback", "赚钱反馈", "missing", "赚钱效应与连板溢价均不可用")
    else:
        severe = ((money_v is not None and money_v <= -2) or
                  (premium_v is not None and premium_v <= -3))
        risk = ((money_v is not None and money_v <= -1) or
                (premium_v is not None and premium_v <= -1))
        feedback = _signal(
            "feedback", "赚钱反馈", "risk" if risk else "healthy",
            f"涨停股中位 {_fmt(money_v, 'pct')} · 连板中位 {_fmt(premium_v, 'pct')}",
            severe=severe, value=money_v,
        )

    promotion = _record(metrics.get("promotion"))
    tier = _record(_record(promotion.get("tiers")).get("1进2"))
    promo_base = _number(tier.get("base"))
    promo = (_rate(tier.get("rate")) if promotion.get("available") is True
             and (promo_base is None or promo_base >= 10) else None)
    promo_values = _trend_values(facts, "promotion_1to2")
    promo_delta = promo_values[-1] - promo_values[-2] if len(promo_values) >= 2 else None
    if promo is None:
        continuation = _signal("continuation", "接力晋级", "missing", "1进2晋级率不可用")
    else:
        risk = promo <= .10 or (promo_delta is not None and promo_delta <= -.08)
        detail = f"1进2 {_fmt(promo, 'rate')}"
        if promo_delta is not None:
            detail += f" · 较前值 {promo_delta:+.0%}"
        continuation = _signal("continuation", "接力晋级", "risk" if risk else "healthy",
                               detail, value=promo)

    seal_quality = _record(facts.get("seal_quality"))
    broken = _rate(seal_quality.get("broken_rate")) if seal_quality.get("available") is True else None
    broken_values = _trend_values(facts, "broken_rate")
    broken_delta = broken_values[-1] - broken_values[-2] if len(broken_values) >= 2 else None
    if broken is None:
        seal = _signal("seal", "封板质量", "missing", "炸板率不可用")
    else:
        severe = broken >= .50
        risk = broken >= .40 or (broken_delta is not None and broken_delta >= .08)
        detail = f"炸板率 {_fmt(broken, 'rate')}"
        if broken_delta is not None:
            detail += f" · 较前值 {broken_delta:+.0%}"
        seal = _signal("seal", "封板质量", "risk" if risk else "healthy",
                       detail, severe=severe, value=broken)

    loss_effect = _record(facts.get("loss_effect"))
    deep_loss = _metric_value(loss_effect, "deep_loss_5_rate", minimum_sample=10, rate=True)
    limit_down = _number(loss_effect.get("market_limit_down")) if loss_effect.get("available") is True else None
    if deep_loss is None and limit_down is None:
        loss = _signal("loss", "亏钱效应", "missing", "大面率与跌停家数均不可用")
    else:
        severe = ((deep_loss is not None and deep_loss >= .30) or
                  (limit_down is not None and limit_down >= 20))
        risk = ((deep_loss is not None and deep_loss >= .20) or
                (limit_down is not None and limit_down >= 10))
        loss = _signal(
            "loss", "亏钱效应", "risk" if risk else "healthy",
            f"昨日强势股跌超5% {_fmt(deep_loss, 'rate')} · 跌停 {_fmt(limit_down)} 家",
            severe=severe, value=deep_loss,
        )

    ladder_gap = _record(metrics.get("ladder_gap"))
    limit_up = _number(promotion.get("limit_up_count"))
    prev_limit_up = _number(promotion.get("prev_limit_up_count"))
    highest = _number(ladder_gap.get("highest")) if ladder_gap.get("available") is True else None
    height_values = _trend_values(facts, "highest_board")
    prev_highest = height_values[-2] if len(height_values) >= 2 else None
    gaps = ladder_gap.get("gaps") if isinstance(ladder_gap.get("gaps"), list) else []
    if limit_up is None and highest is None:
        ladder = _signal("ladder", "涨停梯队", "missing", "涨停家数与最高板不可用")
    else:
        limit_drop = (prev_limit_up - limit_up) if limit_up is not None and prev_limit_up is not None else None
        quantity_risk = bool(limit_drop is not None and limit_drop >= 8
                             and limit_drop / max(prev_limit_up, 1) >= .15)
        height_risk = bool(highest is not None and prev_highest is not None and highest < prev_highest)
        risk = quantity_risk or height_risk or bool(gaps)
        detail = f"涨停 {_fmt(limit_up)} 家 · 最高 {_fmt(highest)} 板"
        if gaps:
            detail += " · 缺档 " + "、".join(f"{g}板" for g in gaps)
        ladder = _signal("ladder", "涨停梯队", "risk" if risk else "healthy",
                         detail, value=limit_up)

    breadth_data = _record(facts.get("breadth"))
    up = _number(breadth_data.get("up")) if breadth_data.get("available") is True else None
    down = _number(breadth_data.get("down")) if breadth_data.get("available") is True else None
    flat = _number(breadth_data.get("flat")) if breadth_data.get("available") is True else None
    deep_up = _number(breadth_data.get("deep_up_5_incl"))
    deep_down = _number(breadth_data.get("deep_down_5"))
    total = (up + down + flat) if None not in (up, down, flat) else None
    up_share = up / total if total else None
    if up_share is None:
        breadth = _signal("breadth", "市场宽度", "missing", "全市场涨跌宽度不可用")
    else:
        distribution_cold = (deep_up is not None and deep_down is not None and deep_down >= deep_up)
        risk = up_share < .45 and ((down or 0) > (up or 0) * 1.2 or distribution_cold)
        breadth = _signal("breadth", "市场宽度", "risk" if risk else "healthy",
                          f"上涨占比 {_fmt(up_share, 'rate')} · {int(up or 0)}涨/{int(down or 0)}跌",
                          value=up_share)

    return [feedback, continuation, seal, loss, ladder, breadth]


def _settled_temperature(metrics: dict) -> tuple[Optional[float], Optional[float]]:
    cycle = _record(metrics.get("cycle"))
    if cycle.get("available") is not True:
        return None, None
    temperature = _rate(cycle.get("current_score"))
    series = [row for row in (cycle.get("series") or []) if isinstance(row, dict)]
    scores = [_rate(row.get("score")) for row in series]
    scores = [score for score in scores if score is not None]
    momentum = scores[-1] - scores[-2] if len(scores) >= 2 else None
    return temperature, momentum


def _phase(temperature: float, momentum: Optional[float], retreat_level: str) -> str:
    move = momentum or 0.0
    if retreat_level == "confirmed":
        return "退潮"
    if temperature <= .25:
        return "冰点"
    if temperature >= .75:
        return "亢奋"
    if retreat_level == "warning" and move < -.03:
        return "退潮"
    if move > .03 and temperature < .50:
        return "修复"
    if temperature >= .45 or move > .03:
        return "发酵"
    return "修复" if move >= -.03 else "退潮"


def _retreat_level(signals: list[dict], temperature: Optional[float],
                   previous: Optional[dict] = None, *, live_persistent: bool = True) -> tuple[str, int, int]:
    risks = [item for item in signals if item["state"] == "risk"]
    core = [item for item in risks if item["key"] in CORE_GROUPS]
    severe = [item for item in risks if item.get("severe")]
    previous_level = _record(_record(previous).get("retreat")).get("level")
    confirmed = (len(risks) >= 3 or (bool(severe) and len(risks) >= 2)
                 or (len(risks) >= 2 and previous_level in ("warning", "confirmed")))
    if confirmed and live_persistent:
        return "confirmed", len(risks), len(core)
    if len(risks) >= 2 and core and live_persistent:
        return "warning", len(risks), len(core)
    if risks or (temperature is not None and temperature >= .75):
        return "watch", len(risks), len(core)
    return "normal", 0, 0


def _confidence(available: int, total: int) -> str:
    if available == total:
        return "high"
    if available >= max(3, total - 2):
        return "medium"
    return "low"


def _summary(phase: Optional[str], level: str, risk_count: int) -> str:
    if phase is None:
        return "核心数据不足，阶段与退潮风险均待确认。"
    if level == "confirmed":
        return f"{risk_count}组独立信号同步转弱，退潮已经得到确认。"
    if level == "warning":
        return f"{risk_count}组独立信号同步转弱，进入退潮预警。"
    if level == "watch":
        return "情绪处于高位或已有单组信号转弱，暂未形成多维退潮确认。"
    return f"当前处于{phase}阶段，核心退潮信号未形成共振。"


def evaluate_settled(env: dict, previous: Optional[dict] = None) -> dict:
    """Evaluate one saved, post-close review without fetching fresh data."""
    metrics = _record(env.get("emotion_metrics"))
    facts = _record(env.get("market_facts"))
    date = env.get("target_date") or env.get("trade_date")
    signals = _settled_signals(env)
    available = sum(item["state"] != "missing" for item in signals)
    temperature, momentum = _settled_temperature(metrics)
    issues = []
    if not date:
        issues.append("复盘日期缺失")
    for source in (metrics, _record(facts.get("breadth"))):
        if source.get("date") and source.get("date") != date:
            issues.append("指标日期与复盘日期不一致")
    sufficient = available >= 4 and temperature is not None and not issues
    if sufficient:
        level, risk_count, core_count = _retreat_level(signals, temperature, previous)
        phase = _phase(temperature, momentum, level)
    else:
        level, risk_count, core_count, phase = "insufficient", 0, 0, None
    confidence = _confidence(available, len(signals)) if sufficient else "low"
    return {
        "version": VERSION,
        "mode": "settled",
        "date": date,
        "as_of": env.get("generated_at") or date,
        "phase": phase,
        "phase_label": phase or "阶段待确认",
        "temperature": round(temperature, 3) if temperature is not None else None,
        "momentum": round(momentum, 3) if momentum is not None else None,
        "confidence": confidence,
        "data_coverage": round(available / len(signals), 3),
        "issues": issues,
        "summary": _summary(phase, level, risk_count),
        "retreat": {
            "level": level,
            "label": {"normal": "正常", "watch": "高位观察", "warning": "退潮预警",
                      "confirmed": "退潮确认", "insufficient": "数据不足"}[level],
            "risk_count": risk_count,
            "core_risk_count": core_count,
            "signals": signals,
        },
    }


def _live_signals(snapshot: dict, history: list[dict], settled: Optional[dict], after_ten: bool) -> list[dict]:
    rows = [row for row in history if isinstance(row, dict)] + [snapshot]
    breaks = [_rate(row.get("break_rate")) for row in rows]
    breaks = [value for value in breaks if value is not None]
    current_break = _rate(snapshot.get("break_rate"))
    if current_break is None:
        seal = _signal("seal", "封板质量", "missing", "盘中炸板率不可用")
    else:
        rise = current_break - min(breaks) if breaks else 0
        risk = current_break >= .40 or rise >= .08
        seal = _signal("seal", "封板质量", "risk" if risk else "healthy",
                       f"炸板率 {_fmt(current_break, 'rate')} · 较日内最佳 {rise:+.0%}",
                       severe=current_break >= .50, value=current_break)

    promotions = [_rate(row.get("promotion_rate")) for row in rows]
    promotions = [value for value in promotions if value is not None]
    promotion_base = _number(snapshot.get("promotion_base"))
    promotion = (_rate(snapshot.get("promotion_rate"))
                 if promotion_base is None or promotion_base >= 10 else None)
    if promotion is None:
        continuation = _signal("continuation", "盘中晋级", "missing", "盘中晋级率不可用")
    else:
        drop = max(promotions) - promotion if promotions else 0
        risk = after_ten and (promotion <= .10 or drop >= .08)
        continuation = _signal("continuation", "盘中晋级", "risk" if risk else "healthy",
                               f"晋级率 {_fmt(promotion, 'rate')} · 较日内峰值 {-drop:+.0%}", value=promotion)

    dt_values = [_number(row.get("dt_count")) for row in rows]
    dt_values = [value for value in dt_values if value is not None]
    dt_count = _number(snapshot.get("dt_count"))
    if dt_count is None:
        loss = _signal("loss", "跌停压力", "missing", "盘中跌停家数不可用")
    else:
        increase = dt_count - min(dt_values) if dt_values else 0
        risk = after_ten and (dt_count >= 10 or increase >= 5)
        loss = _signal("loss", "跌停压力", "risk" if risk else "healthy",
                       f"跌停 {int(dt_count)} 家 · 较日内低点 +{int(increase)} 家",
                       severe=dt_count >= 20, value=dt_count)

    zt_values = [_number(row.get("zt_count")) for row in rows]
    zt_values = [value for value in zt_values if value is not None]
    lb_values = [_number(row.get("lianban_count")) for row in rows]
    lb_values = [value for value in lb_values if value is not None]
    zt_count = _number(snapshot.get("zt_count"))
    lianban = _number(snapshot.get("lianban_count"))
    max_boards = _number(snapshot.get("max_boards"))
    if zt_count is None and lianban is None:
        ladder = _signal("ladder", "盘中梯队", "missing", "涨停与连板家数不可用")
    else:
        zt_peak = max(zt_values) if zt_values else zt_count
        lb_peak = max(lb_values) if lb_values else lianban
        zt_drop = (zt_peak - zt_count) if None not in (zt_peak, zt_count) else 0
        lb_drop = (lb_peak - lianban) if None not in (lb_peak, lianban) else 0
        zt_risk = bool(zt_peak and zt_drop >= 8 and zt_drop / zt_peak >= .15)
        lb_risk = bool(lb_peak and lb_drop >= 2 and lb_drop / lb_peak >= .20)
        base_metrics = _record(_record(settled).get("emotion_metrics"))
        base_height = _number(_record(base_metrics.get("ladder_gap")).get("highest"))
        height_risk = bool(after_ten and max_boards is not None and base_height is not None
                           and max_boards < base_height)
        risk = after_ten and (zt_risk or lb_risk or height_risk)
        ladder = _signal("ladder", "盘中梯队", "risk" if risk else "healthy",
                         f"涨停 {int(zt_count or 0)} 家 · 连板 {int(lianban or 0)} 家 · 最高 {_fmt(max_boards)} 板",
                         value=zt_count)
    return [seal, continuation, loss, ladder]


def _live_temperature(snapshot: dict) -> Optional[float]:
    values = []
    zt = _number(snapshot.get("zt_count"))
    height = _number(snapshot.get("max_boards"))
    broken = _rate(snapshot.get("break_rate"))
    promotion = _rate(snapshot.get("promotion_rate"))
    if zt is not None:
        values.append(min(1.0, zt / 80))
    if height is not None:
        values.append(min(1.0, height / 6))
    if broken is not None:
        values.append(1 - broken)
    if promotion is not None:
        values.append(min(1.0, promotion / .25))
    return sum(values) / len(values) if len(values) >= 3 else None


def evaluate_live(snapshot: dict, history: Optional[list[dict]] = None,
                  settled: Optional[dict] = None) -> dict:
    """Evaluate a live snapshot.  ``history`` contains earlier snapshots today."""
    history = history or []
    as_of = str(snapshot.get("as_of") or "")
    hhmm = as_of[:5]
    valid_time = len(hhmm) == 5 and hhmm[2] == ":" and hhmm.replace(":", "").isdigit()
    before_open_eval = valid_time and hhmm < "09:35"
    after_ten = valid_time and hhmm >= "10:00"
    signals = _live_signals(snapshot, history, settled, after_ten)
    available = sum(item["state"] != "missing" for item in signals)
    temperature = _live_temperature(snapshot)
    previous_temp = _live_temperature(history[-1]) if history else None
    momentum = (temperature - previous_temp) if None not in (temperature, previous_temp) else None
    sufficient = available >= 3 and temperature is not None and valid_time and not before_open_eval

    if not sufficient:
        level, risk_count, core_count, phase = "insufficient", 0, 0, None
    else:
        current_risks = sum(item["state"] == "risk" for item in signals)
        previous_risks = 0
        if history:
            previous_hhmm = str(history[-1].get("as_of") or "")[:5]
            previous_time_valid = (len(previous_hhmm) == 5 and previous_hhmm[2] == ":"
                                   and previous_hhmm.replace(":", "").isdigit())
            previous_after_ten = previous_time_valid and previous_hhmm >= "10:00"
            previous_signals = _live_signals(history[-1], history[:-1], settled, previous_after_ten)
            previous_risks = sum(item["state"] == "risk" for item in previous_signals)
        persistent = after_ten and current_risks >= 2 and previous_risks >= 2
        level, risk_count, core_count = _retreat_level(
            signals, temperature, None, live_persistent=persistent,
        )
        if not after_ten and level in ("warning", "confirmed"):
            level = "watch"
        phase = _phase(temperature, momentum, level)

    summary = ("09:35 前仅采集快照，不判断盘中阶段。" if before_open_eval
               else _summary(phase, level, risk_count))
    return {
        "version": VERSION,
        "mode": "live",
        "date": snapshot.get("date"),
        "as_of": as_of,
        "phase": phase,
        "phase_label": phase or "阶段待确认",
        "temperature": round(temperature, 3) if temperature is not None else None,
        "momentum": round(momentum, 3) if momentum is not None else None,
        "confidence": _confidence(available, len(signals)) if sufficient else "low",
        "data_coverage": round(available / len(signals), 3),
        "issues": [] if sufficient else ["开盘观察或盘中核心数据不足"],
        "summary": summary,
        "retreat": {
            "level": level,
            "label": {"normal": "正常", "watch": "高位观察", "warning": "退潮预警",
                      "confirmed": "退潮确认", "insufficient": "数据不足"}[level],
            "risk_count": risk_count,
            "core_risk_count": core_count,
            "signals": signals,
        },
    }
