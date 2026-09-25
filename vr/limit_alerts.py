"""昨日连板股的盘中炸板事件状态机。"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import astock

BEIJING = timezone(timedelta(hours=8))
_EPSILON = 0.005


def _seconds_between(start: str | None, end: str) -> int:
    try:
        return max(0, int((datetime.strptime(end, "%H:%M:%S") -
                           datetime.strptime(str(start), "%H:%M:%S")).total_seconds()))
    except (TypeError, ValueError):
        return 0


def _number(value) -> float | None:
    try:
        number = float(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


class LimitBreakTracker:
    """按交易日保存观测状态；回封后允许下一次炸板生成新事件。"""

    def __init__(self):
        self.day = ""
        self.states: dict[str, dict] = {}
        self.events: list[dict] = []

    def observe(self, codes: list[str], quotes: dict[str, dict], day: str, as_of: str) -> dict:
        if day != self.day:
            self.day, self.states, self.events = day, {}, []
        requested = list(dict.fromkeys(code for code in codes if code.isdigit() and len(code) == 6))
        for code in requested:
            quote = quotes.get(code) or {}
            price, limit_up = _number(quote.get("price")), _number(quote.get("limit_up"))
            if price is None or limit_up is None:
                continue
            state = self.states.setdefault(code, {"name": str(quote.get("name") or code), "seen_limit": False,
                                                   "status": "watching", "break_count": 0, "last_event_at": None,
                                                   "sealed_since": None, "sealed_observations": 0,
                                                   "last_observed_at": None})
            state["name"] = str(quote.get("name") or state["name"])
            at_limit, was_break = price >= limit_up - _EPSILON, state["status"] == "break"
            if at_limit:
                newly_sealed = not state["seen_limit"] or was_break
                state["seen_limit"] = True
                if was_break:
                    state["status"], state["last_event_at"] = "sealed", as_of
                    self.events.append({"id": f"{day}:{code}:reseal:{state['break_count']}", "kind": "reseal", "code": code,
                                        "name": state["name"], "at": as_of, "price": price, "limit_up": limit_up,
                                        "break_count": state["break_count"]})
                else:
                    state["status"] = "sealed"
                if newly_sealed:
                    state["sealed_since"], state["sealed_observations"] = as_of, 1
                elif state.get("last_observed_at") != as_of:
                    state["sealed_observations"] = int(state.get("sealed_observations") or 0) + 1
            elif state["seen_limit"] and not was_break:
                state["break_count"] += 1
                state["status"], state["last_event_at"] = "break", as_of
                state["sealed_since"], state["sealed_observations"] = None, 0
                self.events.append({"id": f"{day}:{code}:break:{state['break_count']}", "kind": "break", "code": code,
                                    "name": state["name"], "at": as_of, "price": price, "limit_up": limit_up,
                                    "break_count": state["break_count"]})
            elif not state["seen_limit"]:
                state["status"] = "watching"
                state["sealed_since"], state["sealed_observations"] = None, 0
            state["price"], state["limit_up"] = price, limit_up
            state["last_observed_at"] = as_of
        states = {}
        for code in requested:
            if code not in self.states:
                continue
            state = self.states[code]
            public = {key: value for key, value in state.items()
                      if key not in ("seen_limit", "last_observed_at")}
            public["sealed_seconds"] = _seconds_between(state.get("sealed_since"), as_of)
            states[code] = public
        return {"available": bool(states), "date": day, "as_of": as_of, "states": states, "events": list(self.events[-100:])}


_tracker = LimitBreakTracker()
_lock = threading.Lock()


def snapshot(codes: list[str]) -> dict:
    """读取腾讯批量行情；取数失败不伪装成无炸板。"""
    codes = list(dict.fromkeys(code for code in codes if code.isdigit() and len(code) == 6))
    if not codes:
        return {"available": False, "reason": "未提供可监控的股票代码", "states": {}, "events": []}
    try:
        quotes = astock.tencent_quote(codes)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"实时行情取数失败：{type(exc).__name__}", "states": {}, "events": []}
    now = datetime.now(BEIJING)
    with _lock:
        return _tracker.observe(codes, quotes, now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"))


def observe_quotes(codes: list[str], quotes: dict[str, dict], day: str, as_of: str) -> dict:
    """Feed an already-fetched quote batch into the shared break tracker."""
    with _lock:
        return _tracker.observe(codes, quotes, day, as_of)
