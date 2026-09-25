"""Next-session data adapter; historical paths are disk-only."""

from __future__ import annotations

import copy
import sys
import time
import json
from datetime import timedelta
from pathlib import Path

from . import followup_store as store, trade_calendar

_calendar_cache = None


def calendar_days(now) -> list[str]:
    global _calendar_cache
    today = now.date().isoformat()
    if _calendar_cache and _calendar_cache[0] == today and time.monotonic() < _calendar_cache[1]:
        return _calendar_cache[2]
    start = (now.date() - timedelta(days=90)).isoformat()
    days = trade_calendar._ref_dates(start, today)
    # A quote stamped today confirms today's session, but not any missing historical dates.
    if trade_calendar.quote_trade_day() == today and today not in days:
        days.append(today)
        # The historical provider may be temporarily unavailable exactly when
        # the session opens.  In that case only recover the immediately prior
        # session (using trade_calendar's conservative fallback); never invent
        # an arbitrary chain of missing historical trading days.
        if not any(day < today for day in days):
            previous = trade_calendar.prev_trade_date(today)
            if previous and previous < today:
                days.append(previous)
    days = sorted(set(days))
    _calendar_cache = (today, time.monotonic() + (60 if days else 15), days)
    return days


def next_confirmed(date: str, days: list[str]) -> str | None:
    if date not in days:
        return None
    return next((d for d in days if d > date), None)


def _astock():
    if "astock" in sys.modules:
        return sys.modules["astock"]
    from vr import astock
    return astock


def live(eval_date: str, codes: list[str], now) -> dict:
    if trade_calendar.quote_trade_day() != eval_date:
        raise ValueError("实时行情不属于目标交易日")
    source = _astock()
    pools, sources = {}, {}
    for key, endpoint in (("zt", "getTopicZTPool"), ("zb", "getTopicZBPool"), ("dt", "getTopicDTPool")):
        raw = source.em_zt_topic_observed(endpoint, eval_date.replace("-", ""))
        fetched = store.stamp()
        sources[key] = {k: v for k, v in raw.items() if k != "rows"}
        sources[key]["fetched_at"] = fetched
        if raw["available"]:
            pools[key] = [{"code": r["c"], "name": r.get("n", ""), "boards": r.get("lbc"),
                           "sector": r.get("hybk") or ""} for r in raw["rows"]]
        else:
            pools[key] = None
    quotes = source.tencent_quote_observed(codes)
    observed = store.china_now()
    # Requests crossing a session boundary must not be relabelled as the earlier tick.
    if observed.date() != now.date():
        raise ValueError("采集跨过日期边界，本轮不记录")
    sources["quotes"] = {"fetched_at": store.stamp(observed), "available": bool(quotes) or not codes,
                         "requested": len(codes), "returned": len(quotes),
                         "reason": "" if quotes or not codes else "腾讯行情全部未返回"}
    concentration = None
    if pools["zt"]:
        from collections import Counter
        sectors = Counter(r["sector"].strip() or "未分类" for r in pools["zt"])
        if sectors:
            concentration = max(sectors.values()) / len(pools["zt"])
    return {"eval_date": eval_date, "observed_at": store.stamp(observed), "pools": pools,
            "quotes": quotes, "sources": sources, "theme_concentration": concentration}


def merge_close_observations(current: dict, previous: dict | None) -> dict:
    """Retry missing sources without discarding valid same-session close data."""
    from .followup import quote_valid

    ev = current["eval_date"]
    cutoff = store.parse_time(f"{ev}T15:00:00+08:00")
    now = store.parse_time(current.get("observed_at"))
    before = store.parse_time((previous or {}).get("observed_at"))
    if not previous or previous.get("eval_date") != ev or not now or not before or not cutoff <= before <= now:
        return current
    merged = copy.deepcopy(current)
    sources = merged.setdefault("sources", {})
    for key in ("zt", "zb", "dt"):
        if merged["pools"].get(key) is None and previous["pools"].get(key) is not None:
            merged["pools"][key] = copy.deepcopy(previous["pools"][key])
            sources[key] = {**previous.get("sources", {}).get(key, {}), "available": True,
                            "cached": True, "reason": "",
                            "fetched_at": previous.get("sources", {}).get(key, {}).get("fetched_at", previous["observed_at"])}
            if key == "zt":
                merged["theme_concentration"] = previous.get("theme_concentration")
    quotes = merged.setdefault("quotes", {})
    retained = 0
    for code, quote in previous.get("quotes", {}).items():
        if not quote_valid(quotes.get(code, {}), ev, now, True) and quote_valid(quote, ev, now, True):
            quotes[code] = copy.deepcopy(quote)
            retained += 1
    if retained:
        sources["quotes"] = {**sources.get("quotes", {}), "available": True, "cached_count": retained,
                             "returned": len(quotes), "reason": ""}
    return merged


def historical(eval_date: str, codes: list[str]) -> dict | None:
    from . import data, market_facts

    env = store.read_json(Path(market_facts._CACHE_DIR) / f"{eval_date}.json") or {}
    valid = env.get("date") == eval_date and env.get("schema") in market_facts._FACTS_SCHEMA_READABLE
    cached = env.get("pools") or {} if valid else {}
    try:
        previous = json.loads((Path(data._PREV_POOL_DIR) / f"{eval_date}.json").read_text(encoding="utf-8"))
        rows = previous if isinstance(previous, list) else []
    except (OSError, ValueError):
        rows = []
    quotes = {r["code"]: {"pct": r.get("ret"), "price": r.get("close"),
                           "quote_time": None, "settled_date": eval_date}
              for r in rows if r.get("code") in codes and r.get("ret") is not None}
    # Our close observation also stores the complete original cohort for restart recovery.
    if not valid and not quotes:
        return None
    pools = {k: cached.get(k) for k in ("zt", "zb", "dt")}
    review = store.read_json(Path(store.DIR).parent / "reviews" / f"{eval_date}.json") or {}
    concentration = ((review.get("market_facts") or {}).get("theme_structure") or {}).get("concentration")
    if concentration is None and pools["zt"]:
        from collections import Counter
        groups = Counter((r.get("sector") or "").strip() or "未分类" for r in pools["zt"])
        concentration = max(groups.values()) / len(pools["zt"]) if groups else None
    return {"eval_date": eval_date, "observed_at": store.stamp(),
            "pools": pools, "quotes": quotes, "theme_concentration": concentration,
            "sources": {"history": {"available": True, "kind": "已存收盘资料"}}}
