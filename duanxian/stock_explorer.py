"""连板梯队的标的、同行业成员与分钟行情。

复盘页的梯队必须锚定用户选中的历史交易日；同行业成员和行情则来自公开行情接口，
所以每份响应都显式携带 ``quote_date`` / ``requested_date``，前端不把两种日期混在一起。
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Iterable

from . import fetchers, market_facts, trade_calendar


_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: int, loader):
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = loader()
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), value)
    return value


def _number(value):
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clock(value) -> str:
    raw = str(value or "").strip().replace(":", "")
    if raw.endswith(".0"):
        raw = raw[:-2]
    if not raw.isdigit():
        return ""
    raw = raw.zfill(6)
    return f"{raw[:2]}:{raw[2:4]}"


def ladder_details(date: str) -> dict:
    """返回某个已收盘交易日的完整连板标的，绝不回退到别的日期。"""
    pools = market_facts.pools(date)
    if not pools or not isinstance(pools.get("zt"), list):
        return {"available": False, "date": date, "reason": f"{date} 涨停池不可用", "tiers": []}

    raw_by_code = {}
    for raw in ((pools.get("raw") or {}).get("zt") or []):
        code = str(raw.get("代码") or "").zfill(6)
        if re.fullmatch(r"\d{6}", code):
            raw_by_code[code] = raw
    stocks = []
    for row in pools["zt"]:
        boards = int(_number(row.get("boards")) or 1)
        if boards < 2:
            continue
        code = str(row.get("code") or "").zfill(6)
        raw = raw_by_code.get(code, {})
        stocks.append({
            "code": code,
            "name": str(row.get("name") or ""),
            "boards": boards,
            "price": _number(raw.get("最新价")),
            "pct": _number(row.get("ret")),
            "amount": _number(row.get("amount")),
            "float_cap": _number(raw.get("流通市值")),
            "turnover_pct": _number(row.get("turnover")),
            "industry": str(row.get("sector") or ""),
            "reason": "",
            "seal_time": _clock(row.get("first_seal")),
            "last_seal_time": _clock(row.get("last_seal")),
            "break_count": int(_number(row.get("broken_times")) or 0),
            "seal_fund": _number(row.get("seal_fund")),
        })
    stocks.sort(key=lambda item: (-item["boards"], -(item.get("amount") or 0), item["code"]))
    grouped: dict[int, list[dict]] = {}
    for stock in stocks:
        grouped.setdefault(stock["boards"], []).append(stock)
    tiers = [{"boards": boards, "count": len(rows), "stocks": rows}
             for boards, rows in sorted(grouped.items(), reverse=True)]
    return {
        "available": True,
        "date": date,
        "count": len(stocks),
        "highest": max(grouped, default=0),
        "tiers": tiers,
        "reason_note": None,
        "source": "exact_date_limit_up_pool",
    }


def _board_catalog(kind: int) -> list[dict]:
    return _cached(
        f"board-catalog:{kind}", 600,
        lambda: fetchers._clist(  # repository-internal shared Eastmoney client
            f"m:90 t:{kind}", "f3", "f12,f14,f3", pz=100, max_pages=5,
        ),
    )


def _name_match(candidate: str, wanted: str) -> tuple[int, int]:
    candidate, wanted = candidate.strip(), wanted.strip()
    if not candidate or not wanted:
        return (0, 0)
    if candidate == wanted:
        return (3, len(candidate))
    if candidate.startswith(wanted) or wanted.startswith(candidate):
        return (2, min(len(candidate), len(wanted)))
    if len(wanted) >= 3 and wanted in candidate:
        return (1, len(wanted))
    return (0, 0)


def industry_peers(industry: str, focus_code: str = "", limit: int = 300, prefer_concept: bool = False) -> dict:
    """按东财行业板块返回当前全部成分；行业名被上游截短时做保守前缀匹配。"""
    wanted = str(industry or "").strip()
    if not wanted:
        return {"available": False, "reason": "该标的缺少行业归属", "members": []}

    candidates = []
    catalog_order = ((3, "concept"), (2, "industry")) if prefer_concept else ((2, "industry"), (3, "concept"))
    for kind, basis in catalog_order:
        for row in _board_catalog(kind):
            score = _name_match(str(row.get("f14") or ""), wanted)
            if score[0]:
                candidates.append((score, basis, row))
        if candidates:
            break
    if not candidates:
        return {"available": False, "reason": f"未找到“{wanted}”对应的板块", "members": []}

    _, basis, board = max(candidates, key=lambda item: item[0])
    board_code = str(board.get("f12") or "")

    def load_members():
        return fetchers._clist(
            f"b:{board_code}", "f3", "f12,f14,f2,f3,f6,f20,f21,f100",
            pz=100, max_pages=10,
        )

    rows = _cached(f"board-members:{board_code}", 60, load_members)
    members = []
    for row in rows or []:
        code = str(row.get("f12") or "")
        if not re.fullmatch(r"\d{6}", code):
            continue
        members.append({
            "code": code,
            "name": str(row.get("f14") or ""),
            "price": _number(row.get("f2")),
            "pct": _number(row.get("f3")),
            "amount": _number(row.get("f6")),
            "mcap": _number(row.get("f20")),
            "float_cap": _number(row.get("f21")),
            "industry": str(row.get("f100") or wanted),
            "is_focus": code == focus_code,
        })
    members.sort(key=lambda item: (-(item.get("pct") if item.get("pct") is not None else -999), item["code"]))
    return {
        "available": True,
        "board": {"code": board_code, "name": str(board.get("f14") or wanted), "basis": basis},
        "quote_date": trade_calendar.quote_trade_day(),
        "focus_code": focus_code,
        "total": len(members),
        "members": members[:max(1, min(int(limit), 500))],
        "source": "eastmoney_board_constituents",
    }


def _prefix(code: str) -> str:
    if code.startswith(("4", "8", "920")):
        return "bj"
    return "sh" if code.startswith(("6", "9", "5")) else "sz"


def _urlopen(request: urllib.request.Request, timeout: int = 10):
    try:
        from net_utils import market_urlopen  # vr/ is placed on sys.path by server.py
        return market_urlopen(request, timeout=timeout)
    except ImportError:
        return urllib.request.urlopen(request, timeout=timeout)


def _minute_rows(code: str, date: str | None, days: int = 1) -> dict:
    symbol = _prefix(code) + code
    query = urllib.parse.urlencode({"param": f"{symbol},m5,,320"})
    request = urllib.request.Request(
        f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?{query}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
    )
    with _urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    node = (payload.get("data") or {}).get(symbol) or {}
    rows = node.get("m5") or []
    requested = (date or "").replace("-", "")
    parsed_rows = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        stamp = str(row[0])
        day = stamp[:8] if len(stamp) >= 8 else ""
        try:
            if len(day) != 8:
                continue
            parsed_rows.append({
                "date": day,
                "time": stamp[-4:-2] + ":" + stamp[-2:],
                "price": float(row[2]),
                "volume": float(row[5]),
            })
        except (TypeError, ValueError):
            continue
    # 腾讯 m5 一次会带回最近数日。历史复盘页可能请求的是其中某天，因此先截到
    # 目标交易日，再按实际可用的交易日取最后 N 天，不能只按时间字符串硬过滤。
    available_days = sorted({item["date"] for item in parsed_rows if not requested or item["date"] <= requested})
    selected_days = set(available_days[-max(1, min(int(days or 1), 5)):])
    parsed = []
    for item in parsed_rows:
        if item["date"] not in selected_days:
            continue
        day = item["date"]
        item["date"] = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        # 多日分时必须保留日期，否则每日 09:30 的分类轴会相互覆盖。
        item["label"] = f"{day[4:6]}/{day[6:]} {item['time']}"
        parsed.append(item)
    actual_date = available_days[-1] if available_days else ""
    return {"code": code, "date": (f"{actual_date[:4]}-{actual_date[4:6]}-{actual_date[6:]}"
                                            if len(actual_date) == 8 else None), "points": parsed}


def _previous_close(code: str, date: str | None) -> float | None:
    try:
        import astock
        target = date or trade_calendar.quote_trade_day()
        # 当前行情日优先用腾讯实时报价自带的昨收，既少一次 K 线请求，也不会受
        # 未复权日线镜像偶发空载荷影响。
        if target and target == trade_calendar.quote_trade_day():
            quote = (astock.tencent_quote([code]) or {}).get(code) or {}
            previous = _number(quote.get("last_close"))
            if previous and previous > 0:
                return previous
        bars = astock.raw_daily_kline(code, offset=260)
        if not bars:
            bars = astock.kline(code, category=4, offset=260)
    except Exception:
        return None
    if not bars:
        return None
    eligible = [bar for bar in bars if str(bar.get("datetime") or "") <= str(target or "")]
    index = next((i for i in range(len(eligible) - 1, -1, -1)
                  if str(eligible[i].get("datetime") or "") == str(target or "")), None)
    if index is None:
        index = len(eligible) - 1
    if index < 1:
        return None
    return _number(eligible[index - 1].get("close"))


def _one_intraday(code: str, date: str | None, days: int = 1) -> dict:
    result = _minute_rows(code, date, days)
    previous_close = _previous_close(code, result.get("date") or date)
    result["previous_close"] = previous_close
    if previous_close and previous_close > 0:
        for point in result["points"]:
            point["pct"] = round((point["price"] / previous_close - 1) * 100, 3)
    return result


def intraday_series(codes: Iterable[str], date: str | None = None, days: int = 1) -> dict:
    unique = list(dict.fromkeys(str(code).strip() for code in codes
                                if re.fullmatch(r"\d{6}", str(code).strip())))[:6]
    if not unique:
        return {"requested_date": date, "series": {}}
    effective_date = date or trade_calendar.quote_trade_day()
    series: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(unique))) as executor:
        pending = {executor.submit(_one_intraday, code, effective_date, days): code for code in unique}
        for future, code in pending.items():
            try:
                series[code] = future.result()
            except Exception as exc:  # noqa: BLE001
                series[code] = {"code": code, "date": effective_date, "points": [],
                                "reason": f"{type(exc).__name__}: {exc}"}
    return {"requested_date": effective_date, "days": days, "series": series, "source": "tencent_5m_kline"}


def reset_memory_for_tests() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
