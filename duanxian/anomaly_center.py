"""监管异动：复用连阳全市场日线库的版本化本地规则引擎。"""

from __future__ import annotations

import json
import math
import queue
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Iterable

from . import growth_ladders, trade_calendar
from .util import china_now
from net_utils import market_urlopen

ALGORITHM_VERSION = "anomaly-center-v4"
BENCHMARKS = {
    "SH_MAIN": ("SH000002", "上证A股指数"),
    "STAR": ("SH000688", "科创50"),
    "SZ_MAIN": ("SZ399107", "深证A股指数"),
    "GEM": ("SZ399102", "创业板综合指数"),
    "BSE": ("BJ899050", "北证50"),
}

_LOCK = threading.RLock()
_QUEUE: queue.Queue[str | None] = queue.Queue(maxsize=1)
_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_LAST_AUTO_CHECK = 0.0
_STATE = {
    "state": "idle", "phase": "等待同步", "completed": 0, "total": 0,
    "started_at": None, "finished_at": None, "error": None, "trade_date": None,
}


def _num(value, default=None):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def market_profile(code: str, is_st: bool = False) -> dict:
    code = str(code).zfill(6)
    if code.startswith(("43", "83", "87", "92")):
        key, market, board, general, daily, repeats = "BSE", "BJ", "北交所", 40.0, 30.0, 3
    elif code.startswith("68"):
        key, market, board, general, daily, repeats = "STAR", "SH", "科创板", 30.0, 20.0, 3
    elif code.startswith("30"):
        key, market, board, general, daily, repeats = "GEM", "SZ", "创业板", 30.0, 20.0, 3
    elif code.startswith("6"):
        key, market, board, general, daily, repeats = "SH_MAIN", "SH", "沪市主板", 20.0, 10.0, 4
    else:
        key, market, board, general, daily, repeats = "SZ_MAIN", "SZ", "深市主板", 20.0, 10.0, 4
    if is_st and key in {"SH_MAIN", "SZ_MAIN"}:
        general, daily = 12.0, 5.0
    benchmark, benchmark_name = BENCHMARKS[key]
    return {
        "key": key, "market": market, "board": board, "benchmark": benchmark,
        "benchmark_name": benchmark_name, "general_threshold": general,
        "daily_limit": daily, "same_direction_count": repeats,
    }


def _prediction_eligible(instrument: dict) -> bool:
    """Whether the security is covered by the standard anomaly-rule model."""
    name = str(instrument.get("name") or "").strip()
    # Delisting-consolidation securities can contain no-limit sessions and a
    # different monitoring regime.  Do not present them as ordinary A-share
    # severe-anomaly predictions.
    return not ("退市" in name or name.endswith("退"))


def compounded_return(values: Iterable[float]) -> float | None:
    rows = [_num(value) for value in values]
    rows = [value for value in rows if value is not None]
    if len(rows) < 2 or not rows[0]:
        return None
    return (rows[-1] / rows[0] - 1.0) * 100.0


def cumulative_deviation(stock_values: Iterable[float], benchmark_values: Iterable[float]) -> float | None:
    stock_return = compounded_return(stock_values)
    benchmark_return = compounded_return(benchmark_values)
    if stock_return is None or benchmark_return is None:
        return None
    return stock_return - benchmark_return


def required_return(stock_factor: float, benchmark_factor: float, threshold: float) -> float | None:
    """解出今日个股涨跌幅，使区间复合收益相对基准达到 threshold。"""
    if stock_factor <= 0 or benchmark_factor <= 0:
        return None
    target_stock_factor = benchmark_factor + threshold / 100.0
    return (target_stock_factor / stock_factor - 1.0) * 100.0


def _migrate(db) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS benchmark_bars (
            symbol TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL NOT NULL,
            pct_chg REAL, source TEXT NOT NULL, fetched_at TEXT NOT NULL,
            PRIMARY KEY(symbol, trade_date)
        );
        CREATE TABLE IF NOT EXISTS anomaly_events (
            code TEXT NOT NULL, event_date TEXT NOT NULL, kind TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT '', rule_code TEXT NOT NULL DEFAULT '',
            announcement_date TEXT NOT NULL DEFAULT '', reset_date TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '', source TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(code, event_date, kind, rule_code)
        );
        CREATE TABLE IF NOT EXISTS anomaly_baselines (
            trade_date TEXT PRIMARY KEY, algorithm_version TEXT NOT NULL,
            payload_json TEXT NOT NULL, universe_count INTEGER NOT NULL,
            covered_count INTEGER NOT NULL, generated_at TEXT NOT NULL
        );
        """
    )


def _connect():
    db = growth_ladders._connect()
    _migrate(db)
    db.commit()
    return db


def _rows_for_code(db, code: str, end_date: str, limit: int = 80) -> list[dict]:
    rows = db.execute(
        "SELECT trade_date,open,high,low,close,pct_chg,trade_status,is_st,source "
        "FROM daily_bars WHERE code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?",
        (code, end_date, limit),
    ).fetchall()
    return [dict(row) for row in reversed(rows)]


def _benchmark_rows(db, symbol: str, end_date: str, limit: int = 80) -> list[dict]:
    rows = db.execute(
        "SELECT trade_date,close,pct_chg,source FROM benchmark_bars "
        "WHERE symbol=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?",
        (symbol, end_date, limit),
    ).fetchall()
    return [dict(row) for row in reversed(rows)]


def _factor(rows: list[dict], sessions: int) -> float | None:
    """Return a comparable-price factor by compounding stored daily returns.

    The shared corpus intentionally mixes adjusted historical bars with raw
    end-of-day quotes.  Dividing their close prices creates huge, fictitious
    returns at the source boundary.  ``pct_chg`` is comparable across that
    boundary and a proved suspension contributes a flat (0%) session.
    """
    if sessions <= 0:
        return 1.0
    if len(rows) < sessions:
        return None
    factor = 1.0
    for row in rows[-sessions:]:
        if not int(row.get("trade_status", 1)):
            continue
        pct = _num(row.get("pct_chg"))
        if pct is None:
            return None
        factor *= 1.0 + pct / 100.0
    return factor if factor > 0 else None


def _window_deviation(stock_rows: list[dict], benchmark_rows: list[dict], sessions: int) -> float | None:
    sf, bf = _factor(stock_rows, sessions), _factor(benchmark_rows, sessions)
    return (sf - bf) * 100.0 if sf is not None and bf is not None else None


def _candidate(code: str, instrument: dict, stock_rows: list[dict], benchmarks: dict[str, list[dict]]) -> dict | None:
    if not stock_rows:
        return None
    profile = market_profile(code, bool(instrument.get("is_st")))
    # 只有能够证明是上市后最初五个交易日的记录，才允许按“无涨跌幅
    # 限制日”排除。不能用“超过板块日限幅”反推 IPO：复牌、历史 ST
    # 状态偏差以及涨停价四舍五入都会超过固定阈值。
    first_trade_date = str(instrument.get("first_trade_date") or "")
    if first_trade_date and stock_rows[0].get("trade_date") == first_trade_date:
        first_five = {
            row.get("trade_date")
            for row in [item for item in stock_rows if int(item.get("trade_status", 1))][:5]
        }
        stock_rows = [
            row for row in stock_rows
            if not (
                row.get("trade_date") in first_five
                and _num(row.get("pct_chg")) is not None
                and abs(_num(row.get("pct_chg"), 0.0)) > profile["daily_limit"] + 0.5
            )
        ]
    if not stock_rows:
        return None
    benchmark_rows = benchmarks.get(profile["benchmark"], [])
    latest = stock_rows[-1]
    if not benchmark_rows or latest.get("trade_date") != benchmark_rows[-1].get("trade_date"):
        return None
    current_pct = _num(latest.get("pct_chg"), 0.0) or 0.0
    rules = []
    windows = (
        (10, 100.0, "up", "10日累计上涨100%"),
        (10, -50.0, "down", "10日累计下跌50%"),
        (30, 200.0, "up", "30日累计上涨200%"),
        (30, -70.0, "down", "30日累计下跌70%"),
    )
    for sessions, threshold, direction, label in windows:
        deviation = _window_deviation(stock_rows, benchmark_rows, sessions)
        prior_sf = _factor(stock_rows[:-1], sessions - 1)
        prior_bf = _factor(benchmark_rows[:-1], sessions - 1)
        need_today = required_return(prior_sf, prior_bf, threshold) if prior_sf and prior_bf else None
        next_sf, next_bf = _factor(stock_rows, sessions - 1), _factor(benchmark_rows, sessions - 1)
        need_next = required_return(next_sf, next_bf, threshold) if next_sf and next_bf else None
        triggered = deviation is not None and (deviation >= threshold if direction == "up" else deviation <= threshold)
        rules.append({
            "window_days": sessions, "threshold": threshold, "direction": direction,
            "rule": label, "deviation": deviation, "required_today": need_today,
            "required_next": need_next, "triggered": triggered,
        })
    best = min(
        rules,
        key=lambda item: abs(item["required_today"]) if item["required_today"] is not None else 9999,
    )
    return {
        "code": code, "name": instrument.get("name") or code, "market": profile["market"],
        "board": profile["board"], "industry": instrument.get("industry") or "未知",
        "is_st": bool(instrument.get("is_st")), "current_pct": current_pct,
        "price": _num(latest.get("close")), "quote_time": latest.get("trade_date"),
        "benchmark": profile["benchmark"], "benchmark_name": profile["benchmark_name"],
        "daily_limit": profile["daily_limit"], "rules": rules, "best_rule": best,
    }


def _as_prediction(item: dict, rule: dict, status: str, required_key: str) -> dict:
    return {
        "code": item["code"], "name": item["name"], "market": item["market"],
        "board": item["board"], "industry": item["industry"], "price": item["price"],
        "current_pct": item["current_pct"], "window_days": rule["window_days"],
        "deviation": rule["deviation"], "required_pct": rule.get(required_key),
        "status": status, "rule": rule["rule"], "reset_date": None,
        "quote_time": item["quote_time"], "benchmark": item["benchmark"],
        "daily_limit": item["daily_limit"], "direction": rule["direction"],
        "threshold": rule["threshold"],
    }


def _prediction_groups(candidates: list[dict]) -> dict[str, list[dict]]:
    """Choose at most one actionable rule per security and group.

    A one-session prediction must be reachable within that security's single
    daily price limit.  The previous two-limit filter both overstated the
    candidate set and emitted the same security once per matching rule.
    """
    next_day, triggered = [], []
    for item in candidates:
        live_rules = [rule for rule in item["rules"] if not rule["triggered"]]
        reachable = [
            rule for rule in live_rules
            if rule.get("required_next") is not None
            and abs(rule["required_next"]) <= item["daily_limit"] + 0.5
        ]
        if reachable:
            rule = min(reachable, key=lambda row: abs(row["required_next"]))
            next_day.append(_as_prediction(item, rule, "次日预测", "required_next"))

        hit_rules = [rule for rule in item["rules"] if rule["triggered"]]
        if hit_rules:
            # Prefer the rule exceeded by the greatest relative margin, while
            # keeping exactly one row per stock in the summary table.
            rule = max(
                hit_rules,
                key=lambda row: abs(_num(row.get("deviation"), 0.0)) / max(1.0, abs(row["threshold"])),
            )
            triggered.append(_as_prediction(item, rule, "已触发", "required_today"))

    key = lambda row: abs(_num(row.get("required_pct"), 9999))
    return {
        "next_day": sorted(next_day, key=key),
        # 当日预测只能在 09:25 后由实时行情生成，收盘基线不伪装成盘中结果。
        "same_day": [],
        "triggered": sorted(triggered, key=lambda row: -abs(_num(row.get("deviation"), 0.0))),
    }


def _official_rule_window(rule_code: int) -> int:
    return 30 if rule_code in {6, 7, 60, 70} else 10


def _official_prediction_groups(
    items: list[dict],
    instruments: dict[str, dict] | None = None,
    prices: dict[str, dict] | None = None,
) -> dict[str, list[dict]]:
    """Translate the public anomaly feed without reinterpreting its states.

    The source's ``occurrence`` field is the product contract: 0 means a
    same-day candidate, 1 an already-triggered condition, and 2 a next-day
    candidate.  Recomputing these buckets from only the four cumulative
    thresholds drops repetition-rule candidates and changes the result set.
    """
    instruments = instruments or {}
    prices = prices or {}
    buckets = {0: "same_day", 1: "triggered", 2: "next_day"}
    statuses = {0: "盘中预测", 1: "已触发", 2: "次日预测"}
    groups: dict[str, list[dict]] = {"next_day": [], "same_day": [], "triggered": []}
    seen: set[tuple[str, str, int]] = set()
    for source in items:
        occurrence = int(_num(source.get("occurrence"), -1) or 0)
        group = buckets.get(occurrence)
        code = str(source.get("code") or "").zfill(6)
        rule_code = int(_num(source.get("rule_code"), 0) or 0)
        identity = (group or "", code, rule_code)
        if not group or not code.isdigit() or len(code) != 6 or identity in seen:
            continue
        seen.add(identity)
        instrument = instruments.get(code) or {}
        quote = prices.get(code) or {}
        profile = market_profile(code, bool(instrument.get("is_st")))
        target = _num(source.get("target_pct"))
        direction = "down" if target is not None and target < 0 else "up"
        groups[group].append({
            "code": code,
            "name": source.get("name") or instrument.get("name") or code,
            "market": source.get("market") or instrument.get("market") or profile["market"],
            "board": profile["board"],
            "industry": instrument.get("industry") or "未知",
            "price": _num(quote.get("close")),
            "current_pct": _num(source.get("change_pct"), 0.0) or 0.0,
            "window_days": _official_rule_window(rule_code),
            "progress_days": int(_num(source.get("days"), 0) or 0),
            "deviation": _num(source.get("deviation")),
            "required_pct": target,
            "status": statuses[occurrence],
            "rule": source.get("rule") or "严重异常波动",
            "rule_code": rule_code,
            "occurrence": occurrence,
            "reset_date": None,
            "quote_time": source.get("event_date"),
            "benchmark": profile["benchmark"],
            "daily_limit": profile["daily_limit"],
            "direction": direction,
            "source": "eastmoney_price_anomaly",
        })
    for key in ("next_day", "same_day"):
        groups[key].sort(key=lambda row: abs(_num(row.get("required_pct"), 9999)))
    groups["triggered"].sort(key=lambda row: -abs(_num(row.get("deviation"), 0.0)))
    return groups


def _official_feed_date(feed: dict) -> str:
    items = feed.get("items") or []
    if items and items[0].get("event_date"):
        return str(items[0]["event_date"])
    raw = re.sub(r"\D", "", str(feed.get("date") or ""))
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) >= 8 else ""


def _apply_official_predictions(payload: dict, feed: dict) -> dict:
    """Use the public source as the primary current prediction contract."""
    source_date = _official_feed_date(feed)
    if not source_date or source_date != payload.get("trade_date"):
        return payload
    items = feed.get("items") or []
    codes = sorted({str(row.get("code") or "").zfill(6) for row in items})
    instruments: dict[str, dict] = {}
    prices: dict[str, dict] = {}
    if codes:
        placeholders = ",".join("?" for _ in codes)
        with _connect() as db:
            instruments = {
                row["code"]: dict(row)
                for row in db.execute(
                    f"SELECT code,name,market,industry,is_st FROM session_instruments "
                    f"WHERE trade_date=? AND code IN ({placeholders})",
                    (source_date, *codes),
                ).fetchall()
            }
            prices = {
                row["code"]: dict(row)
                for row in db.execute(
                    f"SELECT code,close,pct_chg FROM daily_bars WHERE trade_date=? AND code IN ({placeholders})",
                    (source_date, *codes),
                ).fetchall()
            }
    result = json.loads(json.dumps(payload, ensure_ascii=False))
    result["groups"] = _official_prediction_groups(items, instruments, prices)
    result["available"] = True
    result["updated_at"] = china_now().isoformat(timespec="seconds")
    quality = result.setdefault("quality", {})
    quality["prediction_source"] = "eastmoney_price_anomaly"
    quality["prediction_source_date"] = source_date
    quality["prediction_rows"] = len(items)
    quality["announcement_covered"] = sum(int(row.get("occurrence", -1)) == 1 for row in items)
    hits = quality.setdefault("source_hits", {})
    hits["eastmoney_price_anomaly"] = len(items)
    local_note = str(result.get("note") or "").strip()
    result["note"] = local_note or None
    return result


def compute_predictions(trade_date: str | None = None) -> dict:
    trade_date = trade_date or trade_calendar.latest_session()
    generated_at = china_now().isoformat(timespec="seconds")
    if not trade_date:
        return _empty_predictions("unavailable", "暂时无法确认最近已收盘交易日")
    db = _connect()
    try:
        instruments = [dict(row) for row in db.execute(
            "SELECT code,name,market,industry,trade_status,is_st FROM session_instruments "
            "WHERE trade_date=? ORDER BY code", (trade_date,),
        ).fetchall()]
        if not instruments:
            return _empty_predictions("unavailable", "全市场日线尚未构建", trade_date)
        benchmarks = {symbol: _benchmark_rows(db, symbol, trade_date) for symbol, _ in BENCHMARKS.values()}
        first_trade_dates = {
            row["code"]: row["first_trade_date"]
            for row in db.execute(
                "SELECT code,MIN(trade_date) AS first_trade_date FROM daily_bars GROUP BY code"
            ).fetchall()
        }
        candidates, source_hits, missing_reasons, missing_samples = [], Counter(), Counter(), []
        eligible_instruments = [
            row for row in instruments
            if int(row.get("trade_status", 1)) and _prediction_eligible(row)
        ]
        ineligible_count = sum(
            int(row.get("trade_status", 1)) and not _prediction_eligible(row)
            for row in instruments
        )
        event_count = db.execute("SELECT COUNT(*) AS n FROM anomaly_events WHERE event_date<=?", (trade_date,)).fetchone()["n"]
        for instrument in eligible_instruments:
            rows = _rows_for_code(db, instrument["code"], trade_date)
            instrument["first_trade_date"] = first_trade_dates.get(instrument["code"])
            reset = db.execute(
                "SELECT MAX(reset_date) AS d FROM anomaly_events WHERE code=? AND kind='severe_abnormal' AND reset_date<=?",
                (instrument["code"], trade_date),
            ).fetchone()["d"]
            if reset:
                rows = [row for row in rows if row["trade_date"] >= reset]
            candidate = _candidate(instrument["code"], instrument, rows, benchmarks)
            if candidate:
                candidates.append(candidate)
                if rows:
                    source_hits[str(rows[-1].get("source") or "unknown")] += 1
            else:
                reason = "missing_daily_bars" if not rows else "insufficient_comparable_history"
                if rows and rows[-1].get("trade_date") != trade_date:
                    reason = "latest_daily_bar_missing"
                missing_reasons[reason] += 1
                if len(missing_samples) < 20:
                    missing_samples.append({"code": instrument["code"], "name": instrument.get("name"), "reason": reason})
        groups = _prediction_groups(candidates)
        covered = len(candidates)
        universe = len(eligible_instruments)
        coverage = "full" if universe and covered == universe else ("partial" if covered else "unavailable")
        return {
            "available": bool(covered), "coverage": coverage, "trade_date": trade_date,
            "updated_at": generated_at, "algorithm_version": ALGORITHM_VERSION,
            "groups": groups,
            "quality": {
                "universe_count": universe, "history_covered": covered,
                "announcement_covered": int(event_count), "realtime_covered": 0,
                "missing_count": max(0, universe - covered), "source_hits": dict(source_hits),
                "missing_reasons": dict(missing_reasons), "missing_samples": missing_samples,
                "ineligible_count": ineligible_count,
            },
            "note": None if coverage == "full" else f"当前按已覆盖的 {covered}/{universe} 只股票展示，缺失标的不会解释为空结果。",
        }
    finally:
        db.close()


def _empty_predictions(coverage: str, note: str, trade_date: str | None = None) -> dict:
    return {
        "available": False, "coverage": coverage, "trade_date": trade_date,
        "updated_at": china_now().isoformat(timespec="seconds"), "algorithm_version": ALGORITHM_VERSION,
        "groups": {"next_day": [], "same_day": [], "triggered": []},
        "quality": {"universe_count": 0, "history_covered": 0, "announcement_covered": 0,
                    "realtime_covered": 0, "missing_count": 0, "source_hits": {},
                    "missing_reasons": {}, "missing_samples": [], "ineligible_count": 0},
        "note": note,
    }


def _baseline_is_stale(db, trade_date: str, baseline) -> bool:
    if not baseline:
        return True
    active_rows = db.execute(
        "SELECT name,trade_status FROM session_instruments WHERE trade_date=? AND trade_status<>0",
        (trade_date,),
    ).fetchall()
    # The persisted baseline deliberately excludes delisting-consolidation
    # securities.  Comparing it with the raw active directory made every
    # otherwise-complete baseline look stale forever.
    active = sum(_prediction_eligible(dict(row)) for row in active_rows)
    coverage = db.execute(
        "SELECT updated_at FROM corpus_date_coverage WHERE trade_date=?",
        (trade_date,),
    ).fetchone()
    # Old standalone baselines may predate the shared-corpus coverage table.
    # Without a materialized replacement there is no evidence that they are
    # stale, so keep serving them instead of recomputing an empty payload.
    if not coverage:
        return False
    return (
        int(baseline["universe_count"] or 0) != int(active or 0)
        or str(coverage["updated_at"] or "") > str(baseline["generated_at"] or "")
    )


def _store_baseline(payload: dict) -> None:
    trade_date = payload.get("trade_date")
    quality = payload.get("quality") or {}
    if not trade_date:
        return
    with _connect() as db:
        db.execute(
            "INSERT INTO anomaly_baselines(trade_date,algorithm_version,payload_json,universe_count,covered_count,generated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(trade_date) DO UPDATE SET "
            "algorithm_version=excluded.algorithm_version,payload_json=excluded.payload_json,"
            "universe_count=excluded.universe_count,covered_count=excluded.covered_count,generated_at=excluded.generated_at",
            (trade_date, ALGORITHM_VERSION, json.dumps(payload, ensure_ascii=False),
             int(quality.get("universe_count") or 0), int(quality.get("history_covered") or 0),
             payload.get("updated_at") or china_now().isoformat(timespec="seconds")),
        )
        db.commit()


def _refresh_official_predictions(payload: dict) -> dict:
    from vr import astock

    try:
        return _apply_official_predictions(payload, astock.em_price_anomaly(page_size=500))
    except Exception as exc:  # the verified local model remains the fallback
        result = json.loads(json.dumps(payload, ensure_ascii=False))
        result.setdefault("quality", {})["prediction_source_error"] = str(exc)
        return result


def predictions(trade_date: str | None = None, refresh_official: bool = False) -> dict:
    requested_date = trade_date
    trade_date = trade_date or trade_calendar.latest_session()
    if not trade_date:
        return _empty_predictions("unavailable", "暂时无法确认最近已收盘交易日")
    recompute_date = trade_date
    fallback_note = ""
    db = _connect()
    try:
        row = db.execute(
            "SELECT payload_json,universe_count,covered_count,generated_at FROM anomaly_baselines "
            "WHERE trade_date=? AND algorithm_version=?",
            (trade_date, ALGORITHM_VERSION),
        ).fetchone()
        if row and not _baseline_is_stale(db, trade_date, row):
            payload = json.loads(row["payload_json"])
            if refresh_official and requested_date is None:
                payload = _refresh_official_predictions(payload)
                if (payload.get("quality") or {}).get("prediction_source") == "eastmoney_price_anomaly":
                    _store_baseline(payload)
            return _overlay_live_quotes(payload) if requested_date is None else payload
        # The quote calendar can move to a provisional/new session before the
        # full-market daily store has confirmed it.  A default page request
        # must keep showing the latest completed baseline instead of turning a
        # healthy historical result into an empty screen.  Explicit historical
        # requests remain strict and never silently change their date.
        if requested_date is None:
            fallback = db.execute(
                "SELECT trade_date,payload_json,universe_count,covered_count,generated_at FROM anomaly_baselines "
                "WHERE trade_date<=? AND algorithm_version=? ORDER BY trade_date DESC LIMIT 1",
                (trade_date, ALGORITHM_VERSION),
            ).fetchone()
            if fallback:
                is_older = str(fallback["trade_date"]) < str(trade_date)
                if is_older and not _baseline_is_stale(db, fallback["trade_date"], fallback):
                    payload = json.loads(fallback["payload_json"])
                    original_note = str(payload.get("note") or "").strip()
                    note = f"{trade_date} 全市场日线尚未就绪，暂展示最近完整基线 {fallback['trade_date']}。"
                    payload["note"] = f"{note}{original_note}" if original_note else note
                    return _overlay_live_quotes(payload)
                if is_older:
                    recompute_date = fallback["trade_date"]
                    fallback_note = f"{trade_date} 全市场日线尚未就绪，暂展示最近完整基线 {fallback['trade_date']}。"
    finally:
        db.close()
    payload = compute_predictions(recompute_date)
    if refresh_official and requested_date is None:
        payload = _refresh_official_predictions(payload)
    _store_baseline(payload)
    if fallback_note:
        original_note = str(payload.get("note") or "").strip()
        payload["note"] = f"{fallback_note}{original_note}" if original_note else fallback_note
    return _overlay_live_quotes(payload) if requested_date is None else payload


def _overlay_live_quotes(payload: dict) -> dict:
    """交易时段只刷新已经进入候选集的股票，不做全市场高频抓取。"""
    if (payload.get("quality") or {}).get("prediction_source") == "eastmoney_price_anomaly":
        # The public feed already supplied independently classified same-day,
        # triggered and next-day rows.  Rebuilding same-day rows from the
        # next-day bucket would destroy that contract and duplicate stocks.
        return payload
    now = china_now()
    minute = now.hour * 60 + now.minute
    if now.weekday() >= 5 or not (9 * 60 + 25 <= minute <= 15 * 60):
        return payload
    groups = payload.get("groups") or {}
    # The close baseline's next-day list is today's live candidate pool.  A
    # close snapshot never contains same-day predictions; those are created
    # only after the opening auction from observed quotes.
    base_rows = groups.get("next_day") or []
    codes = list(dict.fromkeys(row.get("code") for row in base_rows if row.get("code")))
    if not codes:
        return payload
    from vr import astock
    quotes = {}
    for start in range(0, len(codes), 50):
        try:
            quotes.update(astock.tencent_quote(codes[start:start + 50]))
        except Exception:
            continue
    result = json.loads(json.dumps(payload, ensure_ascii=False))
    covered, same_day, live_triggered = set(), [], []
    for source_row in base_rows:
        row = dict(source_row)
        quote = quotes.get(row.get("code"))
        if not quote or quote.get("is_stale"):
            continue
        row["price"] = _num(quote.get("price"), row.get("price"))
        row["current_pct"] = _num(quote.get("change_pct"), row.get("current_pct"))
        row["quote_time"] = quote.get("quote_time") or row.get("quote_time")
        row["status"] = "盘中预测"
        covered.add(row.get("code"))
        required = _num(row.get("required_pct"))
        current = _num(row.get("current_pct"))
        direction = row.get("direction")
        reached = (
            required is not None and current is not None
            and (current >= required if direction == "up" else current <= required)
        )
        if reached:
            row["status"] = "已触发"
            live_triggered.append(row)
        else:
            same_day.append(row)
    result.setdefault("groups", {})["same_day"] = sorted(
        same_day, key=lambda row: abs(_num(row.get("required_pct"), 9999) - _num(row.get("current_pct"), 0))
    )
    existing_triggered = {row.get("code"): row for row in result["groups"].get("triggered") or []}
    for row in live_triggered:
        existing_triggered[row.get("code")] = row
    result["groups"]["triggered"] = list(existing_triggered.values())
    result.setdefault("quality", {})["realtime_covered"] = len(covered)
    result["updated_at"] = now.isoformat(timespec="seconds")
    return result


def _days_remaining(end: str) -> int | None:
    try:
        return max(0, (datetime.strptime(end, "%Y-%m-%d").date() - china_now().date()).days)
    except (TypeError, ValueError):
        return None


def monitors(kind: str = "all", page: int = 1, page_size: int = 20) -> dict:
    from vr import astock
    errors, rows = [], []
    try:
        for item in astock.em_stock_monitor(only_active=False):
            rows.append({**item, "type": "risk_warning", "type_label": "风险提示", "remaining_days": _days_remaining(item.get("end"))})
    except Exception as exc:  # noqa: BLE001
        errors.append(f"风险提示：{exc}")
    try:
        for item in astock.em_price_anomaly().get("items", []):
            if int(item.get("occurrence", -1)) != 1:
                continue
            day = item.get("event_date") or ""
            rows.append({
                **item, "type": "severe_abnormal", "type_label": "严重异动",
                "start": day, "end": "", "remaining_days": None,
                "reason": item.get("rule") or "严重异常波动", "announcement_date": day,
            })
    except Exception as exc:  # noqa: BLE001
        errors.append(f"严重异动：{exc}")
    unique = {}
    for row in rows:
        unique[(row.get("code"), row.get("type"), row.get("announcement_date"))] = row
    rows = sorted(unique.values(), key=lambda row: (row.get("announcement_date") or row.get("start") or "", row.get("code") or ""), reverse=True)
    counts = {
        "all": len(rows), "risk_warning": sum(row["type"] == "risk_warning" for row in rows),
        "severe_abnormal": sum(row["type"] == "severe_abnormal" for row in rows),
    }
    filtered = rows if kind == "all" else [row for row in rows if row["type"] == kind]
    start = (page - 1) * page_size
    return {
        "available": bool(rows) or not errors, "coverage": "full" if not errors else ("partial" if rows else "unavailable"),
        "counts": counts, "items": filtered[start:start + page_size],
        "pagination": {"page": page, "page_size": page_size, "total": len(filtered), "pages": max(1, math.ceil(len(filtered) / page_size))},
        "errors": errors, "updated_at": china_now().isoformat(timespec="seconds"),
    }


def search(query: str, limit: int = 10) -> list[dict]:
    text = (query or "").strip()
    if not text:
        return []
    db = _connect()
    try:
        latest = db.execute("SELECT MAX(trade_date) AS d FROM session_instruments").fetchone()["d"]
        if not latest:
            return []
        rows = db.execute(
            "SELECT code,name,market,industry,is_st FROM session_instruments WHERE trade_date=? "
            "AND (code LIKE ? OR name LIKE ?) ORDER BY CASE WHEN code=? THEN 0 WHEN name=? THEN 0 ELSE 1 END, code LIMIT ?",
            (latest, f"{text}%", f"%{text}%", text, text, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def calculate(code: str, trade_date: str | None = None) -> dict:
    code = str(code or "").strip()
    if len(code) != 6 or not code.isdigit():
        raise ValueError("代码必须是 6 位数字")
    trade_date = trade_date or trade_calendar.latest_session()
    if not trade_date:
        raise RuntimeError("暂时无法确认最近已收盘交易日")
    db = _connect()
    try:
        instrument_row = db.execute(
            "SELECT code,name,market,industry,is_st FROM session_instruments WHERE code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 1",
            (code, trade_date),
        ).fetchone()
        if not instrument_row:
            raise ValueError("本地全市场日线尚未收录该股票")
        instrument = dict(instrument_row)
        if not _prediction_eligible(instrument):
            raise ValueError("退市整理期证券不适用本页标准严重异动预测规则")
        profile = market_profile(code, bool(instrument.get("is_st")))
        stock_rows = _rows_for_code(db, code, trade_date)
        reset = db.execute(
            "SELECT MAX(reset_date) AS d FROM anomaly_events WHERE code=? AND kind='severe_abnormal' AND reset_date<=?",
            (code, trade_date),
        ).fetchone()["d"]
        if reset:
            stock_rows = [row for row in stock_rows if row["trade_date"] >= reset]
        benchmark_rows = _benchmark_rows(db, profile["benchmark"], trade_date)
        candidate = _candidate(code, instrument, stock_rows, {profile["benchmark"]: benchmark_rows})
        if not candidate:
            raise RuntimeError(f"缺少 {profile['benchmark_name']} 或个股历史日线，暂时无法计算")
        events = [dict(row) for row in db.execute(
            "SELECT event_date,kind,rule_code,announcement_date,reset_date,url,source FROM anomaly_events WHERE code=? ORDER BY event_date DESC",
            (code,),
        ).fetchall()]
        return {
            "available": True, "trade_date": trade_date, "stock": {k: candidate[k] for k in ("code", "name", "market", "board", "industry", "price", "current_pct")},
            "benchmark": {"code": profile["benchmark"], "name": profile["benchmark_name"]},
            "general_rule": {"threshold": profile["general_threshold"], "same_direction_count": profile["same_direction_count"]},
            "severe_rules": candidate["rules"], "events": events,
            "disclaimer": "本结果为本地规则测算，不代表交易所正式认定；请以交易所公告为准。",
            "algorithm_version": ALGORITHM_VERSION,
        }
    finally:
        db.close()


def _benchmark_rows_from_prices(symbol: str, rows: list, source: str) -> list[dict]:
    output, previous = [], None
    for raw in rows:
        try:
            if isinstance(raw, dict):
                day, close = str(raw.get("day") or raw.get("date") or "")[:10], float(raw.get("close"))
            else:
                day, close = str(raw[0])[:10], float(raw[2])
        except (IndexError, TypeError, ValueError):
            continue
        pct = ((close / previous - 1.0) * 100.0) if previous and close > 0 else None
        output.append({"symbol": symbol, "trade_date": day, "close": close, "pct_chg": pct, "source": source})
        previous = close if close > 0 else previous
    return [row for row in output if row["trade_date"] and row["close"] > 0]


def _fetch_benchmark_tencent(symbol: str, start: str, end: str) -> list[dict]:
    market_symbol = symbol.lower()
    query = urllib.parse.urlencode({"param": f"{market_symbol},day,{start},{end},320,qfq"})
    request = urllib.request.Request(
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?{query}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
    )
    with market_urlopen(request, timeout=12, route="auto") as response:
        payload = json.loads(response.read().decode("utf-8"))
    node = (payload.get("data") or {}).get(market_symbol) or {}
    rows = node.get("qfqday") or node.get("day") or []
    return [row for row in _benchmark_rows_from_prices(symbol, rows, "tencent_qfq") if start <= row["trade_date"] <= end]


def _fetch_benchmark_sina(symbol: str, start: str, end: str) -> list[dict]:
    market_symbol = symbol.lower()
    method = urllib.parse.quote("CN_MarketDataService.getKLineData")
    url = f"https://quotes.sina.cn/cn/api/jsonp.php/var%20_=/{method}?symbol={market_symbol}&scale=240&ma=no&datalen=320"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
    with market_urlopen(request, timeout=12, route="auto") as response:
        text = response.read().decode("utf-8", "ignore")
    match = re.search(r"\(\s*(\[.*\])\s*\)\s*;?\s*$", text, re.S)
    if not match:
        return []
    rows = json.loads(match.group(1))
    return [row for row in _benchmark_rows_from_prices(symbol, rows, "sina_raw") if start <= row["trade_date"] <= end]


def _fetch_benchmark_eastmoney(symbol: str, start: str, end: str) -> list[dict]:
    from vr import astock
    market, code = symbol[:2], symbol[2:]
    secid = ("1." if market == "SH" else "0.") + code
    response = astock.em_get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={"secid": secid, "klt": "101", "fqt": "1", "lmt": 240, "beg": start.replace("-", ""), "end": end.replace("-", ""),
                "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59"},
        headers={"User-Agent": astock.UA, "Referer": "https://quote.eastmoney.com/"}, timeout=15,
    )
    response.raise_for_status()
    lines = ((response.json().get("data") or {}).get("klines") or [])
    output = []
    for line in lines:
        values = str(line).split(",")
        if len(values) >= 9 and _num(values[2]) is not None:
            output.append({"symbol": symbol, "trade_date": values[0], "close": float(values[2]), "pct_chg": _num(values[8]), "source": "eastmoney_qfq"})
    return output


def _fetch_benchmark(symbol: str, start: str, end: str) -> list[dict]:
    """腾讯优先；北证指数等腾讯缺口走新浪，东财作为末级备源。"""
    errors = []
    for loader in (_fetch_benchmark_tencent, _fetch_benchmark_sina, _fetch_benchmark_eastmoney):
        try:
            rows = loader(symbol, start, end)
            if rows:
                return rows
        except Exception as exc:  # each provider is an independent failure domain
            errors.append(f"{loader.__name__}: {exc}")
    raise RuntimeError(f"{symbol} 基准指数历史不可用：{'；'.join(errors) or '所有数据源均返回空'}")


def _run_sync(trade_date: str | None) -> None:
    global _STATE
    target = trade_date or trade_calendar.latest_session()
    started = china_now().isoformat(timespec="seconds")
    with _LOCK:
        _STATE = {"state": "running", "phase": "检查全市场日线", "completed": 0, "total": 7, "started_at": started, "finished_at": None, "error": None, "trade_date": target}
    try:
        if not target:
            raise RuntimeError("暂时无法确认最近已收盘交易日")
        with _connect() as db:
            count = db.execute("SELECT COUNT(*) AS n FROM session_instruments WHERE trade_date=?", (target,)).fetchone()["n"]
            missing_latest = [row["code"] for row in db.execute(
                "SELECT s.code FROM session_instruments s LEFT JOIN daily_bars b "
                "ON b.code=s.code AND b.trade_date=s.trade_date "
                "WHERE s.trade_date=? AND s.trade_status<>0 AND b.code IS NULL ORDER BY s.code",
                (target,),
            ).fetchall()]
        if not count:
            growth_ladders.request_sync(target)
            raise RuntimeError("全市场日线正在构建，请等待连阳数据同步完成后重试")
        if missing_latest:
            with _LOCK:
                _STATE.update({
                    "phase": f"补齐全市场收盘日线（缺 {len(missing_latest)} 只）",
                    "completed": 0,
                })
            growth_ladders.request_sync(target)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                corpus = growth_ladders.status()
                if corpus.get("state") not in {"building", "queued"}:
                    break
                time.sleep(0.5)
            with _connect() as db:
                remaining = [row["code"] for row in db.execute(
                    "SELECT s.code FROM session_instruments s LEFT JOIN daily_bars b "
                    "ON b.code=s.code AND b.trade_date=s.trade_date "
                    "WHERE s.trade_date=? AND s.trade_status<>0 AND b.code IS NULL ORDER BY s.code",
                    (target,),
                ).fetchall()]
            if remaining:
                sample = "、".join(remaining[:10])
                raise RuntimeError(f"全市场收盘日线仍缺 {len(remaining)} 只（{sample}），已保留为缺失而非空结果")
        start = (datetime.strptime(target, "%Y-%m-%d") - timedelta(days=380)).strftime("%Y-%m-%d")
        all_rows = []
        for index, (symbol, _name) in enumerate(BENCHMARKS.values(), 1):
            with _LOCK:
                _STATE.update({"phase": f"同步基准指数 {symbol}", "completed": index - 1})
            all_rows.extend(_fetch_benchmark(symbol, start, target))
        with _connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in all_rows:
                db.execute(
                    "INSERT INTO benchmark_bars(symbol,trade_date,close,pct_chg,source,fetched_at) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(symbol,trade_date) DO UPDATE SET close=excluded.close,pct_chg=excluded.pct_chg,source=excluded.source,fetched_at=excluded.fetched_at",
                    (row["symbol"], row["trade_date"], row["close"], row["pct_chg"], row["source"], china_now().isoformat(timespec="seconds")),
                )
            db.commit()
        from vr import astock
        official_events = []
        with _LOCK:
            _STATE.update({"phase": "同步交易所风险提示", "completed": 5})
        try:
            for row in astock.em_stock_monitor(only_active=False):
                event_day = row.get("announcement_date") or row.get("start") or target
                official_events.append({
                    "code": row["code"], "event_date": event_day, "kind": "risk_warning",
                    "direction": "", "rule_code": "monitor", "announcement_date": event_day,
                    "reset_date": row.get("start") or "", "url": row.get("link") or "",
                    "source": "eastmoney_monitor", "payload": row,
                })
        except Exception:
            pass
        with _LOCK:
            _STATE.update({"phase": "同步严重异常公告", "completed": 6})
        anomaly_feed = None
        try:
            anomaly_feed = astock.em_price_anomaly(page_size=500)
            for row in anomaly_feed.get("items", []):
                # The list endpoint also carries two prediction states.  Only
                # occurrence=1 is an event that may reset a local rule window.
                if int(row.get("occurrence", -1)) != 1:
                    continue
                event_day = row.get("event_date") or target
                official_events.append({
                    "code": row["code"], "event_date": event_day, "kind": "severe_abnormal",
                    "direction": "up" if _num(row.get("deviation"), 0) >= 0 else "down",
                    "rule_code": str(row.get("rule_code") or ""), "announcement_date": event_day,
                    "reset_date": trade_calendar.next_trade_date(event_day) or "", "url": row.get("link") or "",
                    "source": "eastmoney_price_anomaly", "payload": row,
                })
        except Exception:
            pass
        with _connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Older builds persisted prediction candidates as announcements.
            # This feed is a current snapshot, not a historical announcement
            # archive, so replace its rows with the genuinely triggered subset.
            db.execute("DELETE FROM anomaly_events WHERE source='eastmoney_price_anomaly'")
            for row in official_events:
                db.execute(
                    "INSERT INTO anomaly_events(code,event_date,kind,direction,rule_code,announcement_date,reset_date,url,source,payload_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(code,event_date,kind,rule_code) DO UPDATE SET "
                    "announcement_date=excluded.announcement_date,reset_date=excluded.reset_date,url=excluded.url,source=excluded.source,payload_json=excluded.payload_json",
                    (row["code"], row["event_date"], row["kind"], row["direction"], row["rule_code"], row["announcement_date"],
                     row["reset_date"], row["url"], row["source"], json.dumps(row["payload"], ensure_ascii=False)),
                )
            db.commit()
        payload = compute_predictions(target)
        if anomaly_feed:
            payload = _apply_official_predictions(payload, anomaly_feed)
        with _connect() as db:
            quality = payload["quality"]
            db.execute(
                "INSERT INTO anomaly_baselines(trade_date,algorithm_version,payload_json,universe_count,covered_count,generated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(trade_date) DO UPDATE SET algorithm_version=excluded.algorithm_version,payload_json=excluded.payload_json,universe_count=excluded.universe_count,covered_count=excluded.covered_count,generated_at=excluded.generated_at",
                (target, ALGORITHM_VERSION, json.dumps(payload, ensure_ascii=False), quality["universe_count"], quality["history_covered"], payload["updated_at"]),
            )
            db.commit()
        with _LOCK:
            _STATE.update({"state": "complete", "phase": "完成", "completed": 7, "finished_at": china_now().isoformat(timespec="seconds")})
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _STATE.update({"state": "failed", "phase": "同步失败", "error": str(exc), "finished_at": china_now().isoformat(timespec="seconds")})


def _worker() -> None:
    global _LAST_AUTO_CHECK
    # Give an immediately following manual request a brief chance to enter the
    # queue.  Otherwise perform the startup gap check here, never on FastAPI's
    # lifespan thread: latest_session() may need a slow network fallback.
    startup_wait = True
    while not _STOP.is_set():
        try:
            target = _QUEUE.get(timeout=0.05 if startup_wait else 30)
        except queue.Empty:
            now = china_now()
            should_check = startup_wait or (
                now.timestamp() - _LAST_AUTO_CHECK >= 300 and (now.hour, now.minute) >= (15, 10)
            )
            startup_wait = False
            if should_check:
                _LAST_AUTO_CHECK = now.timestamp()
                target = _missing_baseline_date()
                if target:
                    _run_sync(target)
            continue
        startup_wait = False
        if target is None:
            return
        _run_sync(target or None)
        _QUEUE.task_done()


def start_worker() -> None:
    global _THREAD
    with _LOCK:
        if _THREAD and _THREAD.is_alive():
            return
        _STOP.clear()
        _THREAD = threading.Thread(target=_worker, name="anomaly-center-sync", daemon=True)
        _THREAD.start()


def _missing_baseline_date() -> str | None:
    target = trade_calendar.latest_session()
    if not target:
        return None
    db = _connect()
    try:
        latest = db.execute(
            "SELECT MAX(trade_date) AS d FROM session_instruments WHERE trade_date<=?",
            (target,),
        ).fetchone()["d"]
        if not latest:
            return None
        instruments = db.execute(
            "SELECT COUNT(*) AS n FROM session_instruments WHERE trade_date=?", (latest,),
        ).fetchone()["n"]
        baseline = db.execute(
            "SELECT universe_count,covered_count,generated_at FROM anomaly_baselines "
            "WHERE trade_date=? AND algorithm_version=?",
            (latest, ALGORITHM_VERSION),
        ).fetchone()
        stale = bool(baseline and _baseline_is_stale(db, latest, baseline))
        return latest if instruments and (not baseline or stale) else None
    finally:
        db.close()


def stop_worker() -> None:
    _STOP.set()
    try:
        _QUEUE.put_nowait(None)
    except queue.Full:
        pass


def status() -> dict:
    with _LOCK:
        result = dict(_STATE)
    total = int(result.get("total") or 0)
    result["progress"] = round(int(result.get("completed") or 0) / total * 100, 1) if total else 0.0
    db = _connect()
    try:
        row = db.execute("SELECT trade_date,generated_at FROM anomaly_baselines WHERE algorithm_version=? ORDER BY trade_date DESC LIMIT 1", (ALGORITHM_VERSION,)).fetchone()
        result["latest_success_date"] = row["trade_date"] if row else None
        result["updated_at"] = row["generated_at"] if row else None
    finally:
        db.close()
    return result


def request_sync(trade_date: str | None = None) -> dict:
    if trade_date:
        datetime.strptime(trade_date, "%Y-%m-%d")
    start_worker()
    with _LOCK:
        if _STATE.get("state") == "running" or not _QUEUE.empty():
            return status()
        _STATE.update({"state": "queued", "phase": "等待同步", "trade_date": trade_date, "error": None})
    try:
        _QUEUE.put_nowait(trade_date or "")
    except queue.Full:
        pass
    return status()
