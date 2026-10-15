"""连板梯队的标的、同行业成员与分钟行情。

复盘页的梯队必须锚定用户选中的历史交易日；同行业成员优先来自公开行情接口，
主源失败时仅降级到该日冻结涨停样本。每份响应都显式携带
``quote_date`` / ``requested_date``，前端不把两种日期混在一起。
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
_BAOSTOCK_LOCK = threading.Lock()


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


def _exact_review_industry_sample(
    industry: str,
    focus_code: str,
    date: str,
    limit: int,
    source_error: Exception | None = None,
) -> dict:
    """东财板块源不可用时，返回复盘日冻结涨停池中的同行业样本。

    这不是完整行业成分，响应必须显式标成降级样本，不能让前端误解成全行业。
    """
    pools = market_facts.pools(date)
    if not pools or not isinstance(pools.get("zt"), list):
        return {
            "available": False,
            "requested_date": date,
            "reason": f"东财同行业成分暂不可用，且 {date} 冻结涨停池不可用",
            "members": [],
        }

    raw_by_code = {}
    for raw in ((pools.get("raw") or {}).get("zt") or []):
        code = str(raw.get("代码") or "").zfill(6)
        if re.fullmatch(r"\d{6}", code):
            raw_by_code[code] = raw

    members = []
    for row in pools["zt"]:
        sector = str(row.get("sector") or "").strip()
        if not _name_match(sector, industry)[0]:
            continue
        code = str(row.get("code") or "").zfill(6)
        if not re.fullmatch(r"\d{6}", code):
            continue
        raw = raw_by_code.get(code, {})
        members.append({
            "code": code,
            "name": str(row.get("name") or ""),
            "price": _number(raw.get("最新价")),
            "pct": _number(row.get("ret")),
            "amount": _number(row.get("amount")),
            "mcap": None,
            "float_cap": _number(raw.get("流通市值")),
            "turnover_pct": _number(row.get("turnover")),
            "industry": sector or industry,
            "is_focus": code == focus_code,
        })
    members.sort(key=lambda item: (-(item.get("pct") if item.get("pct") is not None else -999), item["code"]))
    if not members:
        return {
            "available": False,
            "requested_date": date,
            "reason": f"东财同行业成分暂不可用；{date} 冻结涨停池中也没有“{industry}”同行业样本",
            "members": [],
        }

    note = f"东财当前同行业成分不可用；仅显示 {date} 复盘日同行业涨停样本，不代表完整行业成分。"
    if source_error:
        note += f" 主源错误：{type(source_error).__name__}。"
    return {
        "available": True,
        "degraded": True,
        "note": note,
        "board": {"code": "", "name": industry, "basis": "review_limit_up_sample"},
        "requested_date": date,
        "quote_date": date,
        "focus_code": focus_code,
        "total": len(members),
        "members": members[:max(1, min(int(limit), 500))],
        "source": "exact_date_limit_up_industry_sample",
    }


def _normalized_industry_name(value: str) -> str:
    name = re.sub(r"^[A-Z]\d{1,3}", "", str(value or "").strip(), flags=re.IGNORECASE)
    for suffix in ("制造业", "服务业", "行业", "业"):
        if len(name) > len(suffix) + 1 and name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name.strip()


def _industry_score(candidate: str, wanted: str) -> tuple[int, int]:
    candidate = _normalized_industry_name(candidate)
    wanted = _normalized_industry_name(wanted)
    score = _name_match(candidate, wanted)
    if score[0]:
        return score
    if candidate and wanted and (candidate in wanted or wanted in candidate):
        return (1, min(len(candidate), len(wanted)))
    return (0, 0)


def _baostock_industry_rows(date: str) -> list[dict]:
    """Return the official classification snapshot as of ``date``."""
    try:
        import baostock as bs
    except ImportError as exc:
        raise RuntimeError("Baostock 行业备用源未安装") from exc
    with _BAOSTOCK_LOCK:
        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(f"Baostock 登录失败：{login.error_code} {login.error_msg}")
        try:
            result = bs.query_stock_industry(date=date)
            if result.error_code != "0":
                raise RuntimeError(f"Baostock 行业查询失败：{result.error_code} {result.error_msg}")
            rows = []
            while result.next():
                values = result.get_row_data()
                rows.append(dict(zip(result.fields, values)))
            return rows
        finally:
            bs.logout()


def _member_quotes(codes: list[str], date: str) -> dict[str, dict]:
    """Enrich members only when the current quote is exactly the requested close."""
    ok, _ = trade_calendar.live_quotes_are_close_of(date)
    if not ok:
        return {}
    from vr.astock import tencent_quote

    quotes: dict[str, dict] = {}
    for start in range(0, len(codes), 50):
        try:
            quotes.update(tencent_quote(codes[start:start + 50]))
        except Exception:  # noqa: BLE001 - names/classification remain useful without quotes
            continue
    return quotes


def _baostock_industry_peers(
    industry: str,
    focus_code: str,
    date: str,
    limit: int,
    source_error: Exception | None = None,
) -> dict:
    rows = _cached(
        f"baostock-industry:{date}", 86400,
        lambda: _baostock_industry_rows(date),
    )
    matched = []
    best = (0, 0)
    for row in rows:
        score = _industry_score(str(row.get("industry") or ""), industry)
        if score > best:
            best, matched = score, [row]
        elif score == best and score[0]:
            matched.append(row)
    if not best[0] or not matched:
        raise ValueError(f"Baostock 未找到“{industry}”对应的证监会行业")

    # A fuzzy match may span similarly named classifications. Retain only the
    # canonical classification selected by the strongest first match.
    canonical = str(matched[0].get("industry") or "")
    matched = [row for row in matched if str(row.get("industry") or "") == canonical]
    codes = [str(row.get("code") or "").split(".")[-1] for row in matched]
    quotes = _member_quotes(codes, date)
    members = []
    for row, code in zip(matched, codes):
        if not re.fullmatch(r"\d{6}", code):
            continue
        quote = quotes.get(code) or {}
        members.append({
            "code": code,
            "name": str(row.get("code_name") or ""),
            "price": _number(quote.get("price")),
            "pct": _number(quote.get("change_pct")),
            "amount": (_number(quote.get("amount_wan")) * 10000 if _number(quote.get("amount_wan")) is not None else None),
            "mcap": (_number(quote.get("mcap_yi")) * 1e8 if _number(quote.get("mcap_yi")) is not None else None),
            "float_cap": (_number(quote.get("float_mcap_yi")) * 1e8 if _number(quote.get("float_mcap_yi")) is not None else None),
            "turnover_pct": _number(quote.get("turnover_pct")),
            "industry": _normalized_industry_name(canonical) or industry,
            "is_focus": code == focus_code,
        })
    members.sort(key=lambda item: (
        -(item.get("pct") if item.get("pct") is not None else -999),
        not item["is_focus"], item["code"],
    ))
    dates = sorted({str(row.get("updateDate") or "") for row in matched if row.get("updateDate")})
    note = f"东财板块源不可用，已切换为 Baostock 的证监会行业分类“{canonical}”；这是完整分类成分，口径可能与东财板块不同。"
    if source_error:
        note += f" 主源错误：{type(source_error).__name__}。"
    return {
        "available": True,
        "degraded": False,
        "note": note,
        "board": {"code": "", "name": _normalized_industry_name(canonical) or industry, "basis": "csrc_industry"},
        "requested_date": date,
        "classification_date": dates[-1] if dates else None,
        "quote_date": date,
        "focus_code": focus_code,
        "total": len(members),
        "members": members[:max(1, min(int(limit), 500))],
        "source": "baostock_csrc_industry",
    }


def _historical_industry_fallback(industry: str, focus_code: str, date: str, limit: int,
                                  source_error: Exception | None = None) -> dict:
    try:
        return _baostock_industry_peers(industry, focus_code, date, limit, source_error)
    except Exception as fallback_error:  # noqa: BLE001 - final honest sample remains available
        return _exact_review_industry_sample(industry, focus_code, date, limit, fallback_error)


def industry_peers(
    industry: str,
    focus_code: str = "",
    limit: int = 300,
    prefer_concept: bool = False,
    date: str | None = None,
) -> dict:
    """按东财行业板块返回当前全部成分；失败时可降级到精确复盘日样本。"""
    wanted = str(industry or "").strip()
    if not wanted:
        return {"available": False, "reason": "该标的缺少行业归属", "members": []}

    try:
        candidates = []
        catalog_order = ((3, "concept"), (2, "industry")) if prefer_concept else ((2, "industry"), (3, "concept"))
        for kind, basis in catalog_order:
            for row in _board_catalog(kind):
                score = _name_match(str(row.get("f14") or ""), wanted)
                if score[0]:
                    candidates.append((score, basis, row))
            if candidates:
                break
    except Exception as exc:  # noqa: BLE001 - 网络源异常要降级，不能让整个抽屉 502
        if date and not prefer_concept:
            return _historical_industry_fallback(wanted, focus_code, date, limit, exc)
        raise
    if not candidates:
        if date and not prefer_concept:
            return _historical_industry_fallback(wanted, focus_code, date, limit)
        return {"available": False, "reason": f"未找到“{wanted}”对应的板块", "members": []}

    _, basis, board = max(candidates, key=lambda item: item[0])
    board_code = str(board.get("f12") or "")

    def load_members():
        return fetchers._clist_page(
            f"b:{board_code}", "f3", "f12,f14,f2,f3,f6,f20,f21,f100",
            pz=max(100, min(int(limit), 100)),
        )

    try:
        rows, member_total = _cached(f"board-members:{board_code}", 60, load_members)
    except Exception as exc:  # noqa: BLE001 - 同上，历史页可使用精确日期冻结样本
        if date and not prefer_concept:
            return _historical_industry_fallback(wanted, focus_code, date, limit, exc)
        raise
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
        "total": member_total,
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


def _select_minute_days(code: str, parsed_rows: list[dict], date: str | None,
                        days: int, source: str) -> dict:
    requested = (date or "").replace("-", "")
    available_days = sorted({
        item["date"] for item in parsed_rows
        if not requested or item["date"] <= requested
    })
    selected_days = set(available_days[-max(1, min(int(days or 1), 5)):])
    parsed = []
    for original in parsed_rows:
        if original["date"] not in selected_days:
            continue
        item = dict(original)
        day = item["date"]
        item["date"] = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        item["label"] = f"{day[4:6]}/{day[6:]} {item['time']}"
        parsed.append(item)
    actual_date = available_days[-1] if available_days else ""
    return {
        "code": code,
        "date": (f"{actual_date[:4]}-{actual_date[4:6]}-{actual_date[6:]}"
                 if len(actual_date) == 8 else None),
        "points": parsed,
        "source": source,
    }


def _eastmoney_minute_rows(code: str, date: str | None, days: int = 1) -> dict:
    """Recent minute bars used only after Tencent returns no points.

    Tencent's m5 endpoint currently returns an empty node for canonical BSE
    920xxx symbols.  Eastmoney trends2 does cover those symbols (market 0) and
    exposes up to five recent sessions.  Keeping this as a fallback preserves
    the low-risk Tencent route for the rest of the market.
    """
    market = "1" if code.startswith(("5", "6", "9")) and not code.startswith("920") else "0"
    query = urllib.parse.urlencode({
        "secid": f"{market}.{code}", "ndays": str(max(1, min(int(days or 1), 5))),
        "iscr": "0", "iscca": "0",
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f53,f54,f55,f56,f57,f58",
    })
    request = urllib.request.Request(
        f"https://push2.eastmoney.com/api/qt/stock/trends2/get?{query}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
    )
    with _urlopen(request, timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8", "ignore"))
    rows = ((payload.get("data") or {}).get("trends") or [])
    parsed_rows = []
    for row in rows:
        columns = str(row).split(",")
        if len(columns) < 5:
            continue
        stamp = columns[0].strip()
        try:
            day, clock = stamp[:10].replace("-", ""), stamp[-5:]
            if len(day) != 8:
                continue
            parsed_rows.append({
                "date": day, "time": clock,
                "price": float(columns[1]), "volume": float(columns[4]),
            })
        except (TypeError, ValueError):
            continue
    return _select_minute_days(code, parsed_rows, date, days, "eastmoney_trends2")


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
    if not parsed_rows and _prefix(code) == "bj":
        try:
            return _eastmoney_minute_rows(code, date, days)
        except Exception:
            # Preserve the primary empty result; _one_intraday still supplies
            # previous_close and the UI can report genuine provider absence.
            pass
    return _select_minute_days(code, parsed_rows, date, days, "tencent_5m_kline")


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
    sources = sorted({str(item.get("source")) for item in series.values() if item.get("source")})
    source = sources[0] if len(sources) == 1 else ("mixed:" + ",".join(sources) if sources else "unavailable")
    return {"requested_date": effective_date, "days": days, "series": series, "source": source}


def reset_memory_for_tests() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
