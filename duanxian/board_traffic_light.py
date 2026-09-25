"""Deterministic traffic lights for board-trading environment and stocks.

The rules are intentionally explicit and versioned.  They are a screening
reference, not a probability model or an order trigger.  Missing observations
fail closed and never become a green light.
"""

from __future__ import annotations

from copy import deepcopy
from math import isfinite
import re
from typing import Optional


VERSION = "board-traffic-light-v2"
LIGHT_LABELS = {"green": "绿灯", "yellow": "黄灯", "red": "红灯", "gray": "数据不足"}


def _record(value) -> dict:
    return value if isinstance(value, dict) else {}


def _number(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if isfinite(value) else None


def _rate(value) -> Optional[float]:
    value = _number(value)
    return value if value is not None and 0 <= value <= 1 else None


def _signal(key: str, label: str, state: str, detail: str,
            value: Optional[float] = None) -> dict:
    return {"key": key, "label": label, "state": state, "detail": detail, "value": value}


def _dimension(key: str, label: str, light: str, summary: str,
               signals: list[dict], missing: Optional[list[str]] = None) -> dict:
    return {"key": key, "label": label, "light": light,
            "light_label": LIGHT_LABELS[light], "summary": summary,
            "signals": signals, "missing": missing or []}


def assess_market(breadth: dict, indices: dict,
                  previous_breadth: Optional[dict] = None, *, mode: str) -> dict:
    """Assess breadth and four major A-share indices as one independent dimension."""
    breadth = _record(breadth)
    indices = _record(indices)
    missing = []
    up = _number(breadth.get("up")) if breadth.get("available") is True else None
    down = _number(breadth.get("down")) if breadth.get("available") is True else None
    if up is None or down is None:
        missing.append("breadth")
    ratio = up / max(down, 1) if up is not None and down is not None else None
    breadth_green = bool(ratio is not None and ratio >= 1.2 and up >= 600)
    breadth_red = bool(ratio is not None and (ratio <= .7 or up < 600))
    signals = [_signal(
        "breadth", "涨跌宽度", "missing" if ratio is None else
        "healthy" if breadth_green else "risk" if breadth_red else "neutral",
        "涨跌家数不可用" if ratio is None else f"{int(up)}涨/{int(down)}跌 · 比值 {ratio:.2f}", ratio,
    )]

    rows = [row for row in (indices.get("items") or []) if isinstance(row, dict)]
    changes = [_number(row.get("change_pct")) for row in rows]
    changes = [value for value in changes if value is not None]
    if indices.get("available") is not True or len(changes) < 4:
        missing.append("indices")
        index_green = index_red = False
        index_detail = f"四大指数仅 {len(changes)}/4 可用"
        index_state = "missing"
    else:
        positive = sum(value >= 0 for value in changes[:4])
        deep_red = sum(value <= -1 for value in changes[:4])
        index_green, index_red = positive >= 3, deep_red >= 3
        index_state = "healthy" if index_green else "risk" if index_red else "neutral"
        index_detail = f"{positive}/4 上涨 · {deep_red}/4 跌超1%"
    signals.append(_signal("indices", "四大指数", index_state, index_detail))

    current_amount = _number(breadth.get("amount_yi"))
    previous_amount = _number(_record(previous_breadth).get("amount_yi"))
    amount_ratio = (current_amount / previous_amount
                    if current_amount is not None and previous_amount and mode == "settled" else None)
    amount_state = ("healthy" if amount_ratio is not None and amount_ratio >= .9 else
                    "risk" if amount_ratio is not None and amount_ratio <= .75 else
                    "neutral" if amount_ratio is not None else "informational")
    amount_detail = (f"较前一交易日 {amount_ratio:.0%}" if amount_ratio is not None else
                     "盘中无可靠同期基准，仅展示" if mode == "live" else "缺少前一交易日成交额")
    signals.append(_signal("amount", "成交额", amount_state, amount_detail, amount_ratio))

    if missing:
        return _dimension("market", "大盘环境", "gray", "宽度或四大指数数据不完整。", signals, missing)
    if up is not None and up < 600:
        return _dimension("market", "大盘环境", "red", "上涨家数不足600家，市场宽度处于冰点。", signals)
    if breadth_green and index_green:
        light, summary = "green", "涨跌宽度与四大指数同步支持。"
    elif breadth_red and index_red:
        light, summary = "red", "涨跌宽度与四大指数同步走弱。"
    else:
        light, summary = "yellow", "宽度与指数没有形成同向确认。"
    return _dimension("market", "大盘环境", light, summary, signals)


def assess_emotion(regime: dict) -> dict:
    regime = _record(regime)
    phase = regime.get("phase")
    retreat = _record(regime.get("retreat"))
    level = retreat.get("level")
    momentum = _number(regime.get("momentum"))
    signals = [
        _signal("phase", "情绪阶段", "missing" if not phase else "observed", phase or "阶段待确认"),
        _signal("retreat", "退潮状态", "missing" if level in (None, "insufficient") else
                "risk" if level in ("warning", "confirmed") else
                "neutral" if level == "watch" else "healthy", retreat.get("label") or "数据不足"),
        _signal("momentum", "阶段动量", "missing" if momentum is None else
                "risk" if momentum < 0 else "healthy", "—" if momentum is None else f"{momentum:+.1%}", momentum),
    ]
    # Keep the six independent inputs visible in the traffic-light payload.
    # The UI can then explain the decision with the exact readings used by the
    # emotion-regime evaluator instead of only showing its derived phase.
    for item in (retreat.get("signals") or []):
        if not isinstance(item, dict):
            continue
        signals.append(_signal(
            f"regime:{item.get('key') or len(signals)}",
            str(item.get("label") or "情绪输入"),
            str(item.get("state") or "missing"),
            str(item.get("detail") or item.get("reason") or "读数不可用"),
            _number(item.get("value")),
        ))
    if not phase or level in (None, "insufficient"):
        return _dimension("emotion", "情绪周期", "gray", "情绪阶段或退潮状态待确认。", signals,
                          ["emotion_regime"])
    if phase in ("冰点", "退潮") or level in ("warning", "confirmed"):
        light, summary = "red", f"{phase} · {retreat.get('label') or '风险状态'}"
    elif phase == "亢奋" or level == "watch" or (momentum is not None and momentum < 0):
        light, summary = "yellow", f"{phase} · {retreat.get('label') or '高位观察'}"
    elif phase in ("修复", "发酵") and level == "normal":
        light, summary = "green", f"{phase} · 核心退潮信号未共振"
    else:
        light, summary = "yellow", f"{phase} · 等待进一步确认"
    return _dimension("emotion", "情绪周期", light, summary, signals)


def assess_board(entry: dict, live_snapshot: Optional[dict] = None) -> tuple[dict, list[str]]:
    entry = _record(entry)
    status = entry.get("status")
    status_light = {"candidate": "green", "observe": "yellow", "wait": "yellow",
                    "blocked": "red", "insufficient": "gray"}.get(status, "gray")
    signals = [_signal("settled_gate", "收盘生态基线",
                       "missing" if status_light == "gray" else
                       "risk" if status_light == "red" else
                       "healthy" if status_light == "green" else "neutral",
                       entry.get("label") or "数据不足")]
    entry_states = {"bullish": "healthy", "bearish": "risk", "neutral": "neutral",
                    "missing": "missing"}
    for item in (entry.get("signals") or []):
        if not isinstance(item, dict):
            continue
        value = _number(item.get("value"))
        unit = item.get("unit")
        if value is None:
            detail = str(item.get("reason") or "读数不可用")
        elif unit == "rate":
            detail = f"当前 {value:.0%}"
        elif unit == "pct":
            detail = f"当前 {value:+.2f}%"
        else:
            detail = f"当前 {value:g}"
        sample = _number(item.get("sample"))
        if sample is not None:
            detail += f" · 样本 {int(sample)}只"
        signals.append(_signal(
            f"entry:{item.get('key') or len(signals)}",
            str(item.get("label") or "收盘生态输入"),
            entry_states.get(str(item.get("state")), "missing"),
            detail,
            value,
        ))
    baseline_vetoes = [f"board:{key}" for key in (entry.get("vetoes") or [])]
    if live_snapshot is None:
        summary = entry.get("reason") or "打板生态数据不足。"
        return _dimension("board", "打板生态", status_light, summary, signals,
                          ["entry_decision"] if status_light == "gray" else []), baseline_vetoes

    live = _record(live_snapshot)
    # The previous close is context for live trading, not a same-day prerequisite.
    # Current complete ecology readings decide the live dimension by themselves.
    signals[0]["state"] = "informational"
    signals[0]["detail"] = f"上一收盘：{entry.get('label') or '无可用基线'}"
    hard_vetoes: list[str] = []
    if live.get("available") is not True:
        signals.append(_signal("live_board", "盘中生态", "missing", live.get("reason") or "盘中数据不可用"))
        return _dimension("board", "打板生态", "gray", "盘中打板数据不完整。", signals,
                          ["live_emotion"]), hard_vetoes
    broken = _rate(live.get("break_rate"))
    promotion = _rate(live.get("promotion_rate"))
    limit_down = _number(live.get("dt_count"))
    live_risks = 0
    for key, label, value, risk, detail in (
        ("broken", "盘中炸板率", broken, broken is not None and broken >= .40,
         "—" if broken is None else f"{broken:.0%}"),
        ("promotion", "盘中晋级率", promotion, promotion is not None and promotion <= .10,
         "—" if promotion is None else f"{promotion:.0%}"),
        ("loss", "盘中跌停压力", limit_down, limit_down is not None and limit_down >= 10,
         "—" if limit_down is None else f"{int(limit_down)}家"),
    ):
        if value is None:
            state = "missing"
        else:
            state = "risk" if risk else "healthy"
            live_risks += int(risk)
        signals.append(_signal(key, label, state, detail, value))
    if broken is not None and broken >= .50:
        hard_vetoes.append("board:live_broken")
    missing = [item["key"] for item in signals[1:] if item["state"] == "missing"]
    if missing:
        light, summary = "gray", "盘中打板生态读数不完整。"
    elif hard_vetoes or live_risks >= 2:
        light, summary = "red", "盘中至少两项生态读数走弱或触发严重炸板风险。"
    elif live_risks == 1:
        light, summary = "yellow", "盘中生态存在一项转弱。"
    else:
        light, summary = "green", "盘中封板生态读数均通过。"
    return _dimension("board", "打板生态", light, summary, signals, missing), hard_vetoes


def _combine(dimensions: dict[str, dict], hard_vetoes: list[str]) -> tuple[str, str]:
    lights = [item["light"] for item in dimensions.values()]
    if "gray" in lights:
        return "gray", "核心维度存在缺失，暂不授予准入资格。"
    red = lights.count("red")
    green = lights.count("green")
    if hard_vetoes or red >= 2:
        return "red", "触发硬否决或至少两个独立维度转弱。"
    if green >= 2 and red == 0:
        return "green", "至少两个独立维度通过，且没有红灯。"
    return "yellow", "环境信号分化，仅适合继续观察。"


def _result(mode: str, date: Optional[str], as_of: object, market: dict,
            emotion: dict, board: dict, hard_vetoes: list[str]) -> dict:
    dimensions = {"market": market, "emotion": emotion, "board": board}
    light, summary = _combine(dimensions, hard_vetoes)
    regime_label = emotion.get("summary") if emotion.get("light") == "yellow" else ""
    suffix = (" · 高位观察" if "高位观察" in str(regime_label) or "亢奋" in str(regime_label)
              else " · 可筛选" if light == "green" else "")
    missing = [f"{key}:{name}" for key, item in dimensions.items() for name in item.get("missing", [])]
    return {
        "version": VERSION, "mode": mode, "date": date, "as_of": as_of,
        "light": light, "label": LIGHT_LABELS[light] + suffix,
        "summary": summary, "dimensions": dimensions,
        "hard_vetoes": list(dict.fromkeys(hard_vetoes)), "missing": missing,
        "reasons": [item["summary"] for item in dimensions.values()],
        "validation": "启发式阈值，尚未回测；绿灯仅表示规则准入，不代表胜率、成交能力或买点。",
        "uncovered": ["Level-2买一封单", "撤单变化", "排队位置", "个人仓位与退出规则"],
    }


def evaluate_settled(env: dict) -> dict:
    facts = _record(env.get("market_facts"))
    entry = _record(env.get("entry_decision"))
    if not entry:
        from .entry_decision import evaluate_entry
        entry = evaluate_entry(env)
    regime = _record(env.get("emotion_regime"))
    if not regime:
        from .emotion_regime import evaluate_settled as evaluate_regime
        regime = evaluate_regime(env)
    market = assess_market(_record(facts.get("breadth")), _record(facts.get("indices")),
                           _record(facts.get("previous_breadth")), mode="settled")
    board, vetoes = assess_board(entry)
    return _result("settled", env.get("target_date") or env.get("trade_date"),
                   env.get("generated_at") or env.get("data_as_of"), market,
                   assess_emotion(regime), board, vetoes)


def evaluate_live(snapshot: dict, breadth: dict, indices: dict, settled: dict) -> dict:
    entry = _record(settled.get("entry_decision"))
    if not entry and settled:
        from .entry_decision import evaluate_entry
        entry = evaluate_entry(settled)
    market = assess_market(breadth, indices, mode="live")
    board, vetoes = assess_board(entry, snapshot)
    result = _result("live", snapshot.get("date"), snapshot.get("as_of"), market,
                     assess_emotion(_record(snapshot.get("emotion_regime"))), board, vetoes)
    result["baseline_date"] = settled.get("target_date") or settled.get("trade_date")
    return result


def _clock_minutes(value: object) -> Optional[int]:
    raw = str(value or "").replace(":", "")
    if len(raw) == 4:
        raw += "00"
    if len(raw) != 6 or not raw.isdigit():
        return None
    hour, minute = int(raw[:2]), int(raw[2:4])
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def _trading_minutes_to_close(value: object) -> Optional[int]:
    minute = _clock_minutes(value)
    if minute is None:
        return None
    morning_close, afternoon_open, close = 11 * 60 + 30, 13 * 60, 15 * 60
    if minute <= morning_close:
        return max(0, morning_close - minute) + (close - afternoon_open)
    if minute < afternoon_open:
        return close - afternoon_open
    return max(0, close - minute)


def _stock_action(environment: dict, quality: dict) -> dict:
    env_light, quality_light = environment.get("light"), quality.get("light")
    if quality_light == "red":
        return {"light": "red", "label": "个股风险", "summary": quality.get("summary") or "个股质量风险。"}
    if quality_light == "gray":
        return {"light": "gray", "label": "质量待确认", "summary": quality.get("summary") or "个股质量数据不足。"}
    if env_light == "red":
        return {"light": "red", "label": "环境红 · 停止新仓", "summary": "环境红灯，停止新开仓；个股质量仍单独保留。"}
    if env_light not in ("green", "yellow"):
        return {"light": "gray", "label": "环境数据缺失", "summary": "环境数据缺失，不给出新开仓准入结论。"}
    if env_light == "yellow":
        prefix = "个股质量通过" if quality_light == "green" else "个股待观察"
        return {"light": "yellow", "label": f"{prefix} · 环境黄 · 暂缓新仓",
                "summary": "环境黄灯，暂缓新开仓；个股质量可用于后续观察。"}
    if quality_light == "green":
        return {"light": "green", "label": "可进入人工筛选", "summary": "环境与个股质量均通过，可进入人工筛选。"}
    return {"light": "yellow", "label": "个股待观察", "summary": quality.get("summary") or "个股质量尚未完全通过。"}


def evaluate_stock(environment: dict, quote: dict, *, state: Optional[dict] = None,
                   board_row: Optional[dict] = None, theme_count: int = 0,
                   theme_members: Optional[list[dict]] = None,
                   industry_count: int = 0, industry_members: Optional[list[dict]] = None,
                   direction: Optional[dict] = None,
                   date: Optional[str] = None, as_of: str = "", mode: str = "live",
                   persistent: bool = False) -> dict:
    """Evaluate stock quality separately from the environment-based entry action."""
    quote, state, board_row = _record(quote), _record(state), _record(board_row)
    theme_members = [_record(member) for member in (theme_members or [])]
    industry_members = [_record(member) for member in (industry_members or [])]
    direction = _record(direction)
    stock_mode = "settled_promotion" if mode == "settled" else "live_entry"
    code = str(quote.get("code") or board_row.get("code") or "")
    name = str(quote.get("name") or board_row.get("name") or code)
    price = _number(quote.get("price") if "price" in quote else board_row.get("price"))
    limit_up = _number(quote.get("limit_up") or quote.get("zt_price"))
    open_price = _number(quote.get("open"))
    turnover = _number(quote.get("turnover_pct") if "turnover_pct" in quote else board_row.get("turnover_pct"))
    vol_ratio = _number(quote.get("vol_ratio"))
    observed_breaks = _number(state.get("break_count"))
    confirmed_breaks = _number(board_row.get("break_count"))
    # The live monitor only sees events after this process began.  A late start
    # therefore reports zero *observed* openings, which must not erase the
    # confirmed cumulative count supplied by the limit-up pool.
    if observed_breaks is None:
        break_number = confirmed_breaks
    elif confirmed_breaks is None:
        break_number = observed_breaks
    else:
        break_number = max(observed_breaks, confirmed_breaks)
    break_count = int(break_number or 0)
    status = state.get("status")
    is_sealed = status == "sealed" or (mode == "settled" and bool(board_row))
    near = bool(price is not None and limit_up and price >= limit_up * .98 and price < limit_up)
    at_limit = bool(price is not None and limit_up and price >= limit_up - .005)
    if at_limit:
        is_sealed = True
    stage = "sealed" if is_sealed else "observe" if near else "not_ready"
    risky_name = any(token in name.upper() for token in ("ST", "退"))
    blockers = (["风险警示或退市标识"] if risky_name else [])
    if status == "break":
        blockers.append("当前处于触板后开板状态")

    env_light = environment.get("light")
    env_label = environment.get("label") or {
        "green": "环境绿灯", "yellow": "环境黄灯", "red": "环境红灯", "gray": "环境数据不足",
    }.get(env_light, "环境数据缺失")
    env_state = ("healthy" if env_light == "green" else "risk" if env_light == "red" else
                 "neutral" if env_light == "yellow" else "missing")
    environment_view = {
        "light": env_light if env_light in LIGHT_LABELS else "gray", "label": str(env_label),
        "date": environment.get("date"), "as_of": environment.get("as_of"),
        "source": "live" if environment.get("mode") == "live" else
                  "settled" if environment.get("mode") == "settled" else "unavailable",
        "missing": list(environment.get("missing") or ([] if env_light in LIGHT_LABELS else ["environment"])),
    }
    environment_signal = _signal("environment", "环境状态", env_state, str(env_label))

    direction_name = str(direction.get("name") or "")
    direction_type = str(direction.get("type") or "")
    industry_name = str(board_row.get("industry") or "").strip()
    catalysts = [item for item in (direction.get("catalysts") or []) if isinstance(item, dict)]

    def theme_signal(theme_available: bool, theme_ok: bool) -> dict:
        if direction:
            if direction_type == "theme" and direction_name:
                detail = f"同题材「{direction_name}」{theme_count} 只封板"
                state_value = "healthy" if theme_ok else "neutral"
            elif catalysts:
                detail = "仅有通用催化，不计题材支持分"
                state_value = "neutral"
            else:
                detail, state_value = "有效题材缺失", "missing"
        else:
            detail = "题材缺失" if not theme_available else f"同方向 {theme_count} 只封板"
            state_value = "missing" if not theme_available else "healthy" if theme_ok else "neutral"
        signal = {**_signal("theme", "题材支持", state_value, detail, theme_count),
                  "members": theme_members, "group_name": direction_name}
        if catalysts:
            signal["catalyst_detail"] = "；".join(
                f"共同催化「{item.get('name')}」{int(item.get('member_count') or 0)} 只，不计题材支持分"
                for item in catalysts if item.get("name")
            )
        return signal

    def industry_signal(industry_available: bool, industry_ok: bool) -> dict:
        detail = (f"同行业「{industry_name}」{industry_count} 只封板"
                  if industry_available else "行业数据缺失")
        return {**_signal("industry", "行业支持",
                          "missing" if not industry_available else "healthy" if industry_ok else "neutral",
                          detail, industry_count),
                "members": industry_members, "group_name": industry_name}
    quality_signals: list[dict] = []
    quality_missing: list[str] = []
    quality_risks: list[str] = []
    score, max_score = 0, 4 if mode == "settled" else 3

    if mode == "settled":
        # 收盘复盘里，涨停池是“是否最终封板”的权威来源。没有 board_row 不是
        # 数据缺失，也不能把昨日强势股误算成今天仍然封板；明确归为不适用，
        # 同时保留固定的收盘涨跌幅供历史回看。
        if not board_row:
            close_return = _number(quote.get("ret") if "ret" in quote else
                                   quote.get("change_pct") if "change_pct" in quote else quote.get("pct"))
            quality_signals = [
                _signal("close_seal", "收盘封板状态", "neutral", "当日未封板"),
                _signal("close_return", "当日涨跌幅", "informational",
                        "—" if close_return is None else f"{close_return:+.2f}%", close_return),
            ]
            quality = {
                "light": "gray", "label": "当日未封板", "score": 0, "max_score": 0,
                "summary": "所选复盘日收盘未封板，封板质量规则不适用。",
                "signals": quality_signals, "risks": [], "missing": [],
            }
            action = {"light": "gray", "label": "封板质量不适用",
                      "summary": "当日未封板，不进入收盘封板质量评分。"}
            return {
                "version": VERSION, "mode": stock_mode, "code": code, "name": name,
                "date": date, "as_of": as_of, "stage": "not_ready",
                "light": "gray", "label": "当日未封板", "summary": quality["summary"],
                "quality": quality, "action": action, "environment": environment_view,
                "signals": [environment_signal, *quality_signals], "blockers": [],
                "missing": list(environment_view["missing"]),
                "uncovered": ["Level-2买一封单", "撤单变化", "排队位置"],
            }
        first_seal = str(board_row.get("seal_time") or "")
        last_seal = str(board_row.get("last_seal_time") or "")
        sealed_minutes = _number(board_row.get("sealed_minutes"))
        if sealed_minutes is None:
            sealed_minutes = _trading_minutes_to_close(last_seal)
        theme_available = (direction_type == "theme" and bool(direction_name)) if direction else bool(board_row.get("reason") or board_row.get("industry"))
        industry_available = bool(industry_name)
        early = bool(first_seal and first_seal <= "10:30")
        turnover_ok = turnover is not None and 3 <= turnover <= 25
        theme_ok = theme_available and theme_count >= 2
        industry_ok = industry_available and industry_count >= 2
        support_ok = theme_ok or industry_ok
        stable_reseal = bool(last_seal and last_seal < "14:30" and
                             sealed_minutes is not None and sealed_minutes >= 30)
        if not first_seal:
            quality_missing.append("seal_time")
        if not last_seal:
            quality_missing.append("last_seal_time")
        if turnover is None:
            quality_missing.append("turnover_pct")
        if break_number is None:
            quality_missing.append("break_count")
        if not theme_available:
            quality_missing.append("theme")

        score += int(early) + int(turnover_ok) + int(support_ok)
        if break_count == 0:
            process_state, process_detail, process_point = "healthy", "未开板，收盘封板", True
        elif break_count <= 2 and stable_reseal:
            process_state, process_detail, process_point = "healthy", f"分歧回封通过 · 稳定 {int(sealed_minutes or 0)} 分钟", True
        elif break_count <= 5 and stable_reseal:
            process_state, process_detail, process_point = "neutral", f"高分歧回封 · 稳定 {int(sealed_minutes or 0)} 分钟", False
        else:
            process_state, process_detail, process_point = "risk", f"回封稳定不足 · {break_count} 次开板", False
        score += int(process_point)

        if break_count >= 6:
            quality_risks.append("开板不少于6次")
        if last_seal and last_seal >= "14:30":
            quality_risks.append("最后封板不早于14:30")
        if sealed_minutes is not None and sealed_minutes < 30:
            quality_risks.append("回封至收盘不足30个交易分钟")

        seal_fund = _number(board_row.get("seal_fund"))
        float_cap = _number(board_row.get("float_cap"))
        fund_ratio = seal_fund / float_cap if seal_fund is not None and float_cap else None
        quality_signals.extend([
            _signal("early_seal", "首次封板", "missing" if not first_seal else "healthy" if early else "neutral", first_seal or "未知"),
            _signal("turnover", "换手率", "missing" if turnover is None else "healthy" if turnover_ok else "neutral",
                    "—" if turnover is None else f"{turnover:.1f}%", turnover),
            theme_signal(theme_available, theme_ok),
            industry_signal(industry_available, industry_ok),
            _signal("seal_process", "封板过程", process_state, process_detail, break_count),
            _signal("last_seal", "最后封板", "missing" if not last_seal else "risk" if last_seal >= "14:30" else "healthy",
                    last_seal or "未知"),
            _signal("sealed_minutes", "回封稳定时长", "missing" if sealed_minutes is None else
                    "healthy" if sealed_minutes >= 30 else "risk",
                    "—" if sealed_minutes is None else f"{int(sealed_minutes)} 个交易分钟", sealed_minutes),
            _signal("seal_fund", "封板资金", "informational",
                    "—" if seal_fund is None else f"{seal_fund / 1e8:.2f} 亿", seal_fund),
            _signal("seal_fund_ratio", "封板资金/流通市值", "informational",
                    "—" if fund_ratio is None else f"{fund_ratio:.2%}", fund_ratio),
        ])

        core_missing = [key for key in quality_missing if key != "theme"]
        if blockers or len(quality_risks) >= 2 or (not core_missing and score <= 1):
            quality_light, quality_label = "red", "质量风险"
        elif core_missing:
            quality_light, quality_label = "gray", "质量待确认"
        elif score >= 3 and not quality_risks and (theme_available or industry_available):
            quality_light, quality_label = "green", "晋级候选"
        else:
            quality_light, quality_label = "yellow", "次日观察"
        if blockers:
            quality_summary = blockers[0]
        elif core_missing:
            quality_summary = "核心字段缺失：" + "、".join(core_missing)
        elif quality_risks:
            quality_summary = "风险项：" + "、".join(quality_risks)
        else:
            quality_summary = f"四项质量因子通过 {score}/4。"
    else:
        hhmm = as_of[:5]
        before_935 = len(hhmm) == 5 and hhmm < "09:35"
        before_1000 = len(hhmm) == 5 and hhmm < "10:00"
        quote_time = str(quote.get("quote_time") or "")
        quote_date_ok = bool(date and quote_time.startswith(str(date).replace("-", "")))
        price_ok = bool(price is not None and open_price is not None and price >= open_price)
        turnover_ok = turnover is not None and 3 <= turnover <= 25
        volume_ok = vol_ratio is not None and vol_ratio >= 1.5
        first_seal = str(board_row.get("seal_time") or "")
        early = bool(first_seal and first_seal <= "10:30")
        theme_available = (direction_type == "theme" and bool(direction_name)) if direction else bool(board_row.get("reason") or board_row.get("industry"))
        theme_ok = theme_available and theme_count >= 2
        industry_available = bool(industry_name)
        industry_ok = industry_available and industry_count >= 2
        support_ok = theme_ok or industry_ok
        observations = int(_number(state.get("sealed_observations")) or 0)
        sealed_seconds = int(_number(state.get("sealed_seconds")) or 0)
        required_seconds = 60 if break_count == 0 else 180 if break_count <= 2 else 300
        stable = observations >= 2 and sealed_seconds >= required_seconds

        if is_sealed:
            for key, value in (("price", price), ("limit_up", limit_up), ("turnover_pct", turnover)):
                if value is None or value <= 0:
                    quality_missing.append(key)
            if not board_row:
                quality_missing.append("limit_pool")
            if not quote_date_ok:
                quality_missing.append("quote_date")
            score = int(early) + int(turnover_ok) + int(support_ok)
            if break_count >= 6:
                quality_risks.append("开板不少于6次")
            quality_signals.extend([
                _signal("limit_pool", "公开涨停池", "healthy" if board_row else "missing",
                        "已确认封板" if board_row else "尚未确认"),
                _signal("early_seal", "首次封板", "missing" if not first_seal else "healthy" if early else "neutral", first_seal or "未知"),
                _signal("turnover", "换手率", "missing" if turnover is None else "healthy" if turnover_ok else "neutral",
                        "—" if turnover is None else f"{turnover:.1f}%", turnover),
                theme_signal(theme_available, theme_ok),
                industry_signal(industry_available, industry_ok),
                _signal("open_record", "开板记录", "risk" if break_count >= 6 else "neutral" if break_count else "healthy",
                        f"今日开板 {break_count} 次", break_count),
                _signal("seal_stability", "封板稳定性", "healthy" if stable else "neutral",
                        f"连续 {observations} 个快照 · {sealed_seconds}/{required_seconds} 秒"),
            ])
            if blockers:
                quality_light, quality_label = "red", "个股风险"
            elif quality_missing:
                quality_light, quality_label = "gray", "质量待确认"
            elif before_935:
                quality_light, quality_label = "gray", "开盘观察"
            elif before_1000 or not stable or not persistent or score < 2 or quality_risks:
                # 已在公开涨停池中，不要把“质量尚未达到盘中准入条件”写成
                # “封板待确认”，否则用户会误以为这只票尚未封板。
                quality_light, quality_label = "yellow", "盘中封板观察"
            else:
                quality_light, quality_label = "green", "打板质量通过"
            quality_summary = (blockers[0] if blockers else
                               "、".join(quality_risks) if quality_risks else
                               (f"已在公开涨停池确认封板；质量因子通过 {score}/3；"
                                f"连续确认 {observations} 个快照、稳定 {sealed_seconds}/{required_seconds} 秒；"
                                "收盘以当日最终封板状态为准。"))
        else:
            for key, value in (("price", price), ("limit_up", limit_up), ("open", open_price),
                               ("turnover_pct", turnover), ("vol_ratio", vol_ratio)):
                if value is None or value <= 0:
                    quality_missing.append(key)
            if not quote_date_ok:
                quality_missing.append("quote_date")
            quality_signals.extend([
                _signal("near_limit", "临板距离", "healthy" if near else "neutral", "距涨停不超过2%" if near else "未到观察区"),
                _signal("price_strength", "日内强度", "missing" if open_price is None else "healthy" if price_ok else "neutral",
                        "现价不低于开盘价" if price_ok else "现价低于开盘价"),
                _signal("vol_ratio", "量比", "missing" if vol_ratio is None else "healthy" if volume_ok else "neutral",
                        "—" if vol_ratio is None else f"{vol_ratio:.2f}", vol_ratio),
                _signal("turnover", "换手率", "missing" if turnover is None else "healthy" if turnover_ok else "neutral",
                        "—" if turnover is None else f"{turnover:.1f}%", turnover),
                _signal("snapshot_confirmation", "连续快照", "healthy" if persistent else "neutral",
                        "已连续确认" if persistent else "等待下一有效快照"),
            ])
            eligible = near and price_ok and turnover_ok and volume_ok and persistent
            if blockers:
                quality_light, quality_label = "red", "个股风险"
            elif quality_missing:
                quality_light, quality_label = "gray", "质量待确认"
            elif stage == "not_ready" or before_935:
                quality_light, quality_label = "gray", "未到观察区" if stage == "not_ready" else "开盘观察"
            elif before_1000 or not eligible:
                quality_light, quality_label = "yellow", "临板观察"
            else:
                quality_light, quality_label = "green", "临板质量通过"
            score = sum((near, price_ok, turnover_ok, volume_ok))
            max_score = 4
            quality_summary = (blockers[0] if blockers else
                               f"临板量价因子通过 {score}/4。")

    quality = {
        "light": quality_light, "label": quality_label, "score": score, "max_score": max_score,
        "summary": quality_summary, "signals": quality_signals,
        "risks": quality_risks, "missing": quality_missing,
    }
    action = _stock_action(environment_view, quality)
    signals = [environment_signal, *quality_signals]
    missing = list(dict.fromkeys([*environment_view["missing"], *quality_missing]))
    return {
        "version": VERSION, "mode": stock_mode, "code": code, "name": name,
        "date": date, "as_of": as_of, "stage": stage,
        "light": action["light"], "label": action["label"], "summary": action["summary"],
        "quality": quality, "action": action, "environment": environment_view,
        "signals": signals, "blockers": blockers, "missing": missing,
        "direction": direction or None,
        "direction_basis": direction.get("basis") if direction else None,
        "uncovered": ["Level-2买一封单", "撤单变化", "排队位置"],
    }


def _pool_hhmm(value: object) -> str:
    raw = str(value or "").replace(":", "").strip()
    if raw.endswith(".0"):
        raw = raw[:-2]
    if raw.isdigit():
        raw = raw.zfill(6)
        if len(raw) == 6 and raw != "000000":
            return f"{raw[:2]}:{raw[2:4]}"
    return ""


def _settled_board_row(row: dict) -> dict:
    """Translate the shared historical limit pool into the stock-light contract."""
    row = _record(row)
    return {
        "code": str(row.get("code") or ""), "name": str(row.get("name") or ""),
        "price": row.get("close"), "pct": row.get("ret"),
        "seal_time": _pool_hhmm(row.get("first_seal")),
        "last_seal_time": _pool_hhmm(row.get("last_seal")),
        "break_count": row.get("broken_times"), "turnover_pct": row.get("turnover"),
        "seal_fund": row.get("seal_fund"), "float_cap": row.get("float_cap"),
        "industry": str(row.get("sector") or ""),
    }


def industry_support_groups(rows) -> dict[str, list[dict]]:
    """Group the same confirmed limit-up pool by its explicit industry field."""
    groups: dict[str, list[dict]] = {}
    for raw in rows or []:
        row = _record(raw)
        industry = str(row.get("industry") or "").strip()
        if not industry:
            continue
        groups.setdefault(industry, []).append({
            key: row.get(key) for key in
            ("code", "name", "seal_time", "last_seal_time", "break_count", "industry")
        })
    return groups


def settled_theme_groups(pool: Optional[dict], reasons: Optional[dict[str, str]] = None) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Build canonical close-time groups using the review event-tree semantics."""
    rows = _record(pool).get("zt")
    if not isinstance(rows, list):
        return {}, {}
    settled_rows = [_settled_board_row(row) for row in rows if isinstance(row, dict)]
    by_code = {row["code"]: row for row in settled_rows if row["code"]}
    reasons = reasons or {}
    from .theme_tree import primary_direction_groups

    directions, by_theme = primary_direction_groups(settled_rows, reasons)
    for code, row in by_code.items():
        row["theme_group"] = directions.get(code)
    return by_code, by_theme


def attach_feedback_stock_lights(review: dict, *, pool: Optional[dict] = None) -> dict:
    """Freeze per-stock close lights into feedback details during serialization only.

    This function is deliberately not called by review_store.load(): viewing an old
    review must never fetch market data or silently rewrite its historical meaning.
    """
    frozen = deepcopy(_record(review))
    facts = _record(frozen.get("market_facts"))
    feedback = _record(facts.get("feedback_matrix"))
    details = feedback.get("details")
    if not isinstance(details, list) or not details:
        return frozen
    date = str(frozen.get("target_date") or frozen.get("trade_date") or "")
    if not date:
        return frozen
    if pool is None:
        from .market_facts import pools
        pool = pools(date)
    if not isinstance(pool, dict) or not isinstance(pool.get("zt"), list):
        return frozen

    reason_map = {str(d.get("code")): str(d.get("reason") or "") for d in details if isinstance(d, dict)}
    by_code, by_theme = settled_theme_groups(pool, reason_map)
    by_industry = industry_support_groups(by_code.values())

    environment = _record(frozen.get("board_traffic_light"))
    enriched = []
    for raw_detail in details:
        detail = deepcopy(_record(raw_detail))
        code = str(detail.get("code") or "")
        board_row = by_code.get(code)
        if board_row:
            direction = _record(board_row.get("theme_group"))
            theme = str(direction.get("name") or "")
            industry = str(board_row.get("industry") or "")
            light = evaluate_stock(
                environment, board_row, board_row=board_row,
                theme_count=len(by_theme.get(theme, [])), theme_members=by_theme.get(theme, []),
                industry_count=len(by_industry.get(industry, [])),
                industry_members=by_industry.get(industry, []),
                direction=direction,
                date=date, as_of="15:00:00", mode="settled", persistent=True,
            )
        else:
            light = evaluate_stock(
                environment, detail, date=date, as_of="15:00:00",
                mode="settled", persistent=True,
            )
        detail["traffic_light"] = light
        enriched.append(detail)

    feedback["details"] = enriched
    facts["feedback_matrix"] = feedback
    frozen["market_facts"] = facts
    return frozen
