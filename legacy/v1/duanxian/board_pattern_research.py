"""Local, versioned research for consecutive-limit-board patterns.

The module deliberately separates two concerns:

* the event directory may be shown as soon as a trustworthy daily pool exists;
* historical matching is only enabled after the requested one-year corpus has
  passed completeness checks.

Eastmoney's limit-up / broken-board pools are used only for the session they
actually report and for intraday enrichment.  Historical close events are
rebuilt from the durable full-market daily-bar corpus, because the live pool
endpoint may silently return today's ``qdate`` for a historical request.
"""

from __future__ import annotations

import json
import math
import os
import queue
import re
import sqlite3
import statistics
import threading
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Iterable

from . import growth_ladders, trade_calendar
from .followup_store import FileLock
from .util import china_now, validate_trade_date


ALGORITHM_VERSION = "board-pattern-v3"
SCHEMA_VERSION = 3
HISTORY_SESSIONS = 250
PRELUDE_SESSIONS = 30
FOLLOWUP_SESSIONS = 3
MAX_CANDIDATES = 800
STAT_SAMPLE = 20
VISIBLE_CASES = 8
LIVE_CACHE_SECONDS = 15.0

_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_WORKER_LOCK = threading.Lock()
_STOP = threading.Event()
_QUEUE: queue.Queue[str | None] = queue.Queue(maxsize=1)
_THREAD: threading.Thread | None = None
_LIVE_LOCK = threading.Lock()
_LIVE_CACHE: dict[str, object] = {}
_RETRY_DELAYS = (300, 900, 1800)
_retry_index = 0
_retry_at = 0.0
_retry_date: str | None = None
_MIGRATION_LOCK = threading.Lock()
_MIGRATED_PATHS: set[str] = set()


class SyncFailure(RuntimeError):
    """A sync error carrying whether an automatic retry can make progress."""

    def __init__(self, message: str, *, retriable: bool = True):
        super().__init__(message)
        self.retriable = retriable


def _now() -> str:
    return china_now().isoformat(timespec="seconds")


def _number(value, default=None):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _connect() -> sqlite3.Connection:
    db = growth_ladders._connect()  # same durable market corpus by design
    key = str(growth_ladders._db_path().resolve())
    if key not in _MIGRATED_PATHS:
        with _MIGRATION_LOCK:
            if key not in _MIGRATED_PATHS:
                _migrate(db)
                _MIGRATED_PATHS.add(key)
    return db


def _read_connect() -> sqlite3.Connection:
    key = str(growth_ladders._db_path().resolve())
    if key not in _MIGRATED_PATHS:
        bootstrap = _connect()
        bootstrap.close()
    return growth_ladders._read_connect()


def _migrate(db: sqlite3.Connection) -> None:
    columns = {row[1] for row in db.execute("PRAGMA table_info(daily_bars)")}
    if "turnover_pct" not in columns:
        db.execute("ALTER TABLE daily_bars ADD COLUMN turnover_pct REAL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS pattern_limit_events (
            event_date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            market TEXT NOT NULL,
            industry TEXT NOT NULL DEFAULT '未知',
            theme TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            board_height INTEGER NOT NULL,
            board_type TEXT NOT NULL,
            path_json TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            prev_close REAL,
            pct_chg REAL,
            volume REAL,
            amount REAL,
            turnover_pct REAL,
            float_mcap REAL,
            first_seal TEXT,
            break_times INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            PRIMARY KEY (event_date, code)
        );
        CREATE INDEX IF NOT EXISTS idx_pattern_events_code_date
            ON pattern_limit_events(code, event_date);
        CREATE INDEX IF NOT EXISTS idx_pattern_events_date_status
            ON pattern_limit_events(event_date, status, board_height);
        CREATE TABLE IF NOT EXISTS pattern_day_snapshots (
            trade_date TEXT PRIMARY KEY,
            algorithm_version TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            coverage TEXT NOT NULL,
            event_count INTEGER NOT NULL,
            source_hits_json TEXT NOT NULL DEFAULT '{}',
            generated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pattern_match_cache (
            trade_date TEXT NOT NULL,
            code TEXT NOT NULL,
            algorithm TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            PRIMARY KEY (trade_date, code, algorithm, algorithm_version)
        );
        CREATE TABLE IF NOT EXISTS pattern_sync_runs (
            run_id TEXT PRIMARY KEY,
            requested_date TEXT,
            state TEXT NOT NULL,
            phase TEXT NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0,
            missing_json TEXT NOT NULL DEFAULT '[]',
            source_hits_json TEXT NOT NULL DEFAULT '{}',
            error TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pattern_sync_failures (
            run_id TEXT NOT NULL,
            trade_date TEXT NOT NULL DEFAULT '',
            phase TEXT NOT NULL,
            source TEXT NOT NULL,
            error_type TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'provider_network',
            message TEXT NOT NULL,
            retriable INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, trade_date, phase, source)
        );
        CREATE INDEX IF NOT EXISTS idx_pattern_failures_run
            ON pattern_sync_failures(run_id, retriable);
        """
    )
    run_columns = {row[1] for row in db.execute("PRAGMA table_info(pattern_sync_runs)")}
    if "updated_at" not in run_columns:
        db.execute("ALTER TABLE pattern_sync_runs ADD COLUMN updated_at TEXT")
    failure_columns = {row[1] for row in db.execute("PRAGMA table_info(pattern_sync_failures)")}
    if "category" not in failure_columns:
        db.execute("ALTER TABLE pattern_sync_failures ADD COLUMN category TEXT NOT NULL DEFAULT 'provider_network'")
    db.execute(
        "INSERT INTO metadata(key,value) VALUES('pattern_schema_version',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    db.commit()


@contextmanager
def _transaction():
    with _LOCK:
        db = _connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def _market(code: str) -> str:
    if code.startswith(("43", "83", "87", "92")):
        return "BJ"
    return "SH" if code.startswith(("60", "68")) else "SZ"


def market_limit_pct(code: str, is_st: bool = False) -> float:
    if is_st:
        return 5.0
    if code.startswith(("30", "68")):
        return 20.0
    if code.startswith(("43", "83", "87", "92")):
        return 30.0
    return 10.0


def at_limit(price: float | None, limit_price: float | None, tolerance: float = 0.0045) -> bool:
    if not price or not limit_price:
        return False
    return abs(price - limit_price) <= max(0.01, abs(limit_price) * tolerance)


def classify_board_type(open_: float | None, high: float | None, low: float | None,
                        close: float | None, limit_price: float | None,
                        first_seal: str = "", break_times: int = 0) -> str:
    """Classify a sealed limit-up day as 一字/T字/换手.

    Pool metadata is accepted because the Eastmoney event pool does not expose
    OHLC.  When OHLC is present it remains the authoritative branch.
    """
    if limit_price and all(at_limit(value, limit_price) for value in (open_, high, low, close)):
        return "一字"
    if limit_price and at_limit(open_, limit_price) and at_limit(close, limit_price):
        return "T字" if (low and not at_limit(low, limit_price)) or break_times > 0 else "一字"
    if first_seal.startswith("09:25"):
        return "T字" if break_times > 0 else "一字"
    return "换手"


def same_path_suffix(left: Iterable[str], right: Iterable[str]) -> int:
    count = 0
    for a, b in zip(reversed(list(left)), reversed(list(right))):
        if a != b:
            break
        count += 1
    return count


def _hhmmss(value) -> str:
    if not value:
        return ""
    raw = str(value).split(".")[0].zfill(6)
    return f"{raw[:2]}:{raw[2:4]}:{raw[4:6]}"


def _raw_height(row: dict) -> int:
    stat = row.get("zttj") or {}
    for value in (row.get("lbc"), stat.get("ct"), row.get("ylbc")):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue
    match = re.search(r"(\d+)\s*板", str(row.get("high_days") or ""))
    return int(match.group(1)) if match else 1


def _bar_map(day: str, codes: Iterable[str]) -> dict[str, dict]:
    values = list(dict.fromkeys(codes))
    if not values:
        return {}
    with _connect() as db:
        rows = db.execute(
            "SELECT b.*,si.name,si.market,si.industry FROM daily_bars b "
            "LEFT JOIN session_instruments si ON si.trade_date=b.trade_date AND si.code=b.code "
            f"WHERE b.trade_date=? AND b.code IN ({','.join('?' for _ in values)})",
            (day, *values),
        )
    return {row["code"]: dict(row) for row in rows}


def _previous_events(day: str) -> dict[str, dict]:
    previous = trade_calendar.prev_trade_date(day)
    if not previous:
        return {}
    with _connect() as db:
        return {row["code"]: dict(row) for row in db.execute(
            "SELECT * FROM pattern_limit_events WHERE event_date=?", (previous,),
        )}


def _parse_pools(day: str, zt_rows: list[dict], zb_rows: list[dict], *, live: bool) -> list[dict]:
    previous = _previous_events(day)
    codes = [str(row.get("c") or "").zfill(6) for row in [*zt_rows, *zb_rows]]
    bars = _bar_map(day, codes)
    result: list[dict] = []
    for sealed, rows in ((True, zt_rows), (False, zb_rows)):
        for raw in rows:
            code = str(raw.get("c") or "").zfill(6)
            if not re.fullmatch(r"\d{6}", code):
                continue
            bar = bars.get(code, {})
            prior = previous.get(code)
            raw_height = _raw_height(raw)
            height = raw_height if sealed else int((prior or {}).get("board_height") or max(1, raw_height - 1))
            if height < 2:
                continue
            name = str(raw.get("n") or bar.get("name") or code)
            is_st = bool(bar.get("is_st") or "ST" in name.upper())
            close = _number(bar.get("close"), _number(raw.get("p"), 0.0) / 1000.0)
            pct = _number(bar.get("pct_chg"), _number(raw.get("zdp"), 0.0))
            prev_close = close / (1 + pct / 100) if close and pct is not None and pct > -99 else None
            limit_price = round((prev_close or 0) * (1 + market_limit_pct(code, is_st) / 100), 2) if prev_close else _number(raw.get("ztp"), 0.0) / 1000.0
            first_seal = _hhmmss(raw.get("fbt"))
            breaks = int(_number(raw.get("zbc"), 0) or 0)
            board_type = classify_board_type(
                _number(bar.get("open")), _number(bar.get("high")), _number(bar.get("low")), close,
                limit_price, first_seal, breaks,
            ) if sealed else "断板"
            status = ("sealed" if sealed else ("temporary_break" if live else "confirmed_break"))
            amount = _number(bar.get("amount"), _number(raw.get("amount")))
            turnover = _number(bar.get("turnover_pct"), _number(raw.get("hs")))
            float_mcap = _number(raw.get("ltsz"))
            if not float_mcap and close and turnover and amount:
                float_mcap = amount / max(turnover / 100, 1e-8)
            result.append({
                "event_date": day, "code": code, "name": name, "market": _market(code),
                "industry": str(raw.get("hybk") or bar.get("industry") or "未知"),
                "theme": str(raw.get("reason_type") or ""), "status": status,
                "board_height": height, "board_type": board_type, "path": [],
                "open": _number(bar.get("open")), "high": _number(bar.get("high")),
                "low": _number(bar.get("low")), "close": close, "prev_close": prev_close,
                "pct_chg": pct, "volume": _number(bar.get("volume")), "amount": amount,
                "turnover_pct": turnover, "float_mcap": float_mcap,
                "first_seal": first_seal, "break_times": breaks,
                "source": "eastmoney_zt_pool", "observed_at": _now(),
            })
    return result


def _rebuild_from_daily_bars(day: str, *, live: bool = False) -> list[dict]:
    """Full-corpus fallback.  It is only trusted when the day is fully covered."""
    with _connect() as db:
        universe = db.execute("SELECT COUNT(*) FROM session_instruments WHERE trade_date=?", (day,)).fetchone()[0]
        rows = [dict(row) for row in db.execute(
            "SELECT b.*,si.name,si.market,si.industry FROM daily_bars b JOIN session_instruments si "
            "ON si.trade_date=b.trade_date AND si.code=b.code WHERE b.trade_date=?", (day,),
        )]
    if not universe or len(rows) < universe:
        raise RuntimeError(f"{day} 本地日线覆盖 {len(rows)}/{universe}，不能重建完整事件池")
    previous = _previous_events(day)
    sealed_codes = [
        row["code"] for row in rows
        if row.get("trade_status") and row.get("pct_chg") is not None
        and float(row["pct_chg"]) >= market_limit_pct(row["code"], bool(row.get("is_st"))) - 0.15
    ]
    histories: dict[str, list[dict]] = {code: [] for code in sealed_codes}
    if sealed_codes:
        with _connect() as lookup:
            for start in range(0, len(sealed_codes), 500):
                batch = sealed_codes[start:start + 500]
                marks = ",".join("?" for _ in batch)
                for item in lookup.execute(
                    "SELECT code,trade_date,pct_chg,is_st,trade_status FROM ("
                    "SELECT code,trade_date,pct_chg,is_st,trade_status,"
                    "ROW_NUMBER() OVER(PARTITION BY code ORDER BY trade_date DESC) AS rn "
                    f"FROM daily_bars WHERE code IN ({marks}) AND trade_date<=?) WHERE rn<=30 "
                    "ORDER BY code,trade_date DESC",
                    (*batch, day),
                ):
                    histories[item["code"]].append(dict(item))

    def sealed_streak(code: str) -> int:
        count = 0
        for item in histories.get(code, []):
            if not item.get("trade_status"):
                break
            threshold = market_limit_pct(code, bool(item.get("is_st")))
            if item.get("pct_chg") is None or float(item["pct_chg"]) < threshold - 0.15:
                break
            count += 1
        return count
    result = []
    for row in rows:
        if not row.get("trade_status") or row.get("pct_chg") is None:
            continue
        code = row["code"]
        limit_pct = market_limit_pct(code, bool(row.get("is_st")))
        sealed = float(row["pct_chg"]) >= limit_pct - 0.15
        prior = previous.get(code)
        prior_height = (
            int((prior or {}).get("board_height") or 0)
            if (prior or {}).get("status") == "sealed"
            else 0
        )
        if not sealed and prior_height < 2:
            continue
        height = sealed_streak(code) if sealed else prior_height
        if height < 2:
            continue
        prev_close = row["close"] / (1 + row["pct_chg"] / 100) if row.get("close") else None
        limit_price = round(prev_close * (1 + limit_pct / 100), 2) if prev_close else None
        result.append({
            "event_date": day, "code": code, "name": row.get("name") or code,
            "market": row.get("market") or _market(code), "industry": row.get("industry") or "未知",
            "theme": "", "status": "sealed" if sealed else ("temporary_break" if live else "confirmed_break"),
            "board_height": height,
            "board_type": classify_board_type(row.get("open"), row.get("high"), row.get("low"), row.get("close"), limit_price) if sealed else "断板",
            "path": [], "open": row.get("open"), "high": row.get("high"), "low": row.get("low"),
            "close": row.get("close"), "prev_close": prev_close, "pct_chg": row.get("pct_chg"),
            "volume": row.get("volume"), "amount": row.get("amount"),
            "turnover_pct": row.get("turnover_pct"), "float_mcap": None,
            "first_seal": "", "break_times": 0, "source": "local_full_daily_rebuild", "observed_at": _now(),
        })
    return result


def _fetch_events(day: str, *, live: bool = False) -> tuple[list[dict], dict[str, int]]:
    from vr import astock

    compact = day.replace("-", "")
    zt = astock.em_zt_topic_observed("getTopicZTPool", compact)
    zb = astock.em_zt_topic_observed("getTopicZBPool", compact)
    if zt.get("available") and zb.get("available"):
        return _parse_pools(day, zt["rows"], zb["rows"], live=live), {
            "eastmoney_limit_pool": len(zt["rows"]), "eastmoney_break_pool": len(zb["rows"]),
        }
    events = _rebuild_from_daily_bars(day, live=live)
    return events, {"local_full_daily_rebuild": len(events)}


def _quote_date(quote: dict) -> str:
    timestamp = str(quote.get("quote_time") or "")
    if len(timestamp) >= 8 and timestamp[:8].isdigit():
        return f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}"
    return ""


def _apply_live_quote(event: dict, quote: dict, day: str) -> dict:
    if _quote_date(quote) != day or quote.get("is_stale"):
        return event
    price = _number(quote.get("price"))
    if not price:
        return event
    open_ = _number(quote.get("open")) or price
    high = _number(quote.get("high")) or price
    low = _number(quote.get("low")) or price
    previous = _number(quote.get("last_close"))
    return {
        **event,
        "name": str(quote.get("name") or event.get("name") or event["code"]),
        "open": open_, "high": high, "low": low, "close": price,
        "prev_close": previous, "pct_chg": _number(quote.get("change_pct")),
        "volume": _number(quote.get("volume_hands"), 0.0) * 100,
        "amount": _number(quote.get("amount_wan"), 0.0) * 10_000,
        "turnover_pct": _number(quote.get("turnover_pct")),
        "float_mcap": _number(quote.get("float_mcap_yi"), 0.0) * 100_000_000,
        "observed_at": _now(), "source": f"{event.get('source') or 'live_pool'}+tencent_live",
    }


def _fetch_live_events(day: str) -> tuple[list[dict], dict[str, int]]:
    """Build the 09:25–15:00 event pool from current pools and quotes.

    Besides stocks already present in today's limit-up/broken-board pools, the
    previous session's 2-board+ sealed stocks remain eligible and are marked as
    temporary breaks until they actually seal again.
    """
    from vr import astock

    compact = day.replace("-", "")
    zt = astock.em_zt_topic_observed("getTopicZTPool", compact)
    zb = astock.em_zt_topic_observed("getTopicZBPool", compact)
    if not zt.get("available") or not zb.get("available"):
        reasons = "；".join(filter(None, [str(zt.get("reason") or ""), str(zb.get("reason") or "")]))
        raise RuntimeError(f"当日涨停/炸板池尚未完整发布：{reasons or '未知原因'}")
    items = _parse_pools(day, zt["rows"], zb["rows"], live=True)
    previous = _previous_events(day)
    prior_sealed = {
        code: _row_event(row) for code, row in previous.items()
        if row.get("status") == "sealed" and int(row.get("board_height") or 0) >= 2
    }
    by_code = {item["code"]: item for item in items}
    codes = list(dict.fromkeys([*by_code, *prior_sealed]))
    quotes: dict[str, dict] = {}
    for start in range(0, len(codes), 50):
        try:
            quotes.update(astock.tencent_quote(codes[start:start + 50]))
        except Exception:
            continue
    valid_quotes = {code: quote for code, quote in quotes.items() if _quote_date(quote) == day and not quote.get("is_stale")}
    for code, event in list(by_code.items()):
        quote = valid_quotes.get(code)
        if quote:
            by_code[code] = _apply_live_quote(event, quote, day)
    for code, prior in prior_sealed.items():
        if code in by_code or code not in valid_quotes:
            continue
        quote = valid_quotes[code]
        price = _number(quote.get("price"))
        limit_price = _number(quote.get("limit_up"))
        sealed = at_limit(price, limit_price)
        board_type = (
            classify_board_type(
                _number(quote.get("open")) or price, _number(quote.get("high")) or price,
                _number(quote.get("low")) or price, price, limit_price,
            ) if sealed else "断板"
        )
        event = {
            **prior, "event_date": day, "status": "sealed" if sealed else "temporary_break",
            "board_height": int(prior["board_height"]) + (1 if sealed else 0),
            "board_type": board_type, "path": [], "first_seal": "", "break_times": 0,
            "source": "previous_high_board+tencent_live", "observed_at": _now(),
        }
        by_code[code] = _apply_live_quote(event, quote, day)
    return list(by_code.values()), {
        "eastmoney_limit_pool": len(zt["rows"]), "eastmoney_break_pool": len(zb["rows"]),
        "tencent_live": len(valid_quotes), "previous_high_board": len(prior_sealed),
    }


def _path_from_daily_bars(db: sqlite3.Connection, event: dict) -> list[str]:
    """Reconstruct a complete board path without requiring first-board events.

    The public event pool intentionally starts at the second board, so chaining
    only ``pattern_limit_events`` can never recover the first step.  The corpus
    has one membership row per market session; use it to make missing bars and
    suspension days hard boundaries instead of silently bridging them.
    """
    height = int(event["board_height"])
    if height < 1:
        return []
    operator = "<=" if event["status"] == "sealed" else "<"
    expected_dates = [row[0] for row in db.execute(
        f"SELECT trade_date FROM session_instruments WHERE code=? AND trade_date{operator}? "
        "ORDER BY trade_date DESC LIMIT ?",
        (event["code"], event["event_date"], height),
    )][::-1]
    if len(expected_dates) != height:
        return []
    marks = ",".join("?" for _ in expected_dates)
    bars = [dict(row) for row in db.execute(
        "SELECT trade_date,open,high,low,close,pct_chg,trade_status,is_st FROM daily_bars "
        f"WHERE code=? AND trade_date IN ({marks}) ORDER BY trade_date",
        (event["code"], *expected_dates),
    )]
    if [bar["trade_date"] for bar in bars] != expected_dates:
        return []
    path: list[str] = []
    for bar in bars:
        if not bar.get("trade_status") or bar.get("pct_chg") is None or not bar.get("close"):
            return []
        limit_pct = market_limit_pct(event["code"], bool(bar.get("is_st")))
        if float(bar["pct_chg"]) < limit_pct - 0.15:
            return []
        prev_close = float(bar["close"]) / (1 + float(bar["pct_chg"]) / 100)
        limit_price = round(prev_close * (1 + limit_pct / 100), 2)
        path.append(classify_board_type(
            bar.get("open"), bar.get("high"), bar.get("low"), bar.get("close"), limit_price,
        ))
    return path


def _path_for(db: sqlite3.Connection, event: dict, previous_day: str | None = None) -> list[str]:
    height = int(event["board_height"])
    reconstructed = _path_from_daily_bars(db, event)
    if len(reconstructed) == height:
        return reconstructed
    if height <= 1 and event["status"] == "sealed":
        return [event["board_type"]]
    if previous_day is None:
        previous_day = trade_calendar.prev_trade_date(event["event_date"])
    if not previous_day:
        return [event["board_type"]]
    prior = db.execute(
        "SELECT board_height,board_type,status,path_json FROM pattern_limit_events "
        "WHERE code=? AND event_date=?",
        (event["code"], previous_day),
    ).fetchone()
    if not prior or prior["status"] != "sealed":
        return [event["board_type"]]
    try:
        path = json.loads(prior["path_json"] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        path = []
    if event["status"] != "sealed":
        return path if int(prior["board_height"]) == height and len(path) == height else [event["board_type"]]
    if int(prior["board_height"]) != height - 1:
        return [event["board_type"]]
    if not path or len(path) != height - 1:
        return [event["board_type"]]
    return [*path, event["board_type"]]


def _store_day(day: str, events: list[dict], source_hits: dict[str, int], *, coverage: str = "full",
               previous_day: str | None = None) -> dict:
    market_env = _market_environment(events)
    resolved_previous = trade_calendar.prev_trade_date(day) if previous_day is None else previous_day
    with _transaction() as db:
        db.execute("DELETE FROM pattern_limit_events WHERE event_date=?", (day,))
        for event in sorted(events, key=lambda item: (item["board_height"], item["code"])):
            event["path"] = _path_for(db, event, resolved_previous)
            db.execute(
                "INSERT INTO pattern_limit_events("
                "event_date,code,name,market,industry,theme,status,board_height,board_type,path_json,"
                "open,high,low,close,prev_close,pct_chg,volume,amount,turnover_pct,float_mcap,"
                "first_seal,break_times,source,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event["event_date"], event["code"], event["name"], event["market"], event["industry"],
                 event["theme"], event["status"], event["board_height"], event["board_type"],
                 json.dumps(event["path"], ensure_ascii=False), event["open"], event["high"], event["low"],
                 event["close"], event["prev_close"], event["pct_chg"], event["volume"], event["amount"],
                 event["turnover_pct"], event["float_mcap"], event["first_seal"], event["break_times"],
                 event["source"], event["observed_at"]),
            )
            if event.get("turnover_pct") is not None:
                db.execute("UPDATE daily_bars SET turnover_pct=? WHERE code=? AND trade_date=?",
                           (event["turnover_pct"], event["code"], day))
        payload = {
            "date": day, "events": events, "market_environment": market_env,
            "coverage": coverage, "source_hits": source_hits, "updated_at": _now(),
        }
        db.execute(
            "INSERT INTO pattern_day_snapshots VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(trade_date) DO UPDATE SET algorithm_version=excluded.algorithm_version,"
            "payload_json=excluded.payload_json,coverage=excluded.coverage,event_count=excluded.event_count,"
            "source_hits_json=excluded.source_hits_json,generated_at=excluded.generated_at",
            (day, ALGORITHM_VERSION, json.dumps(payload, ensure_ascii=False), coverage, len(events),
             json.dumps(source_hits, ensure_ascii=False), _now()),
        )
        db.execute("DELETE FROM pattern_match_cache WHERE trade_date=?", (day,))
    return payload


def _market_environment(events: list[dict]) -> dict:
    sealed = [event for event in events if event["status"] == "sealed"]
    broken = [event for event in events if event["status"] != "sealed"]
    total = len(sealed) + len(broken)
    return {
        "limit_up_count": len(sealed), "break_count": len(broken),
        "break_rate": round(len(broken) / total * 100, 2) if total else 0.0,
        "max_height": max((int(event["board_height"]) for event in sealed), default=0),
        "promotion_rate": round(sum(int(event["board_height"]) >= 2 for event in sealed) / len(sealed) * 100, 2) if sealed else 0.0,
        "index_pct": 0.0,
    }


def _row_event(row: sqlite3.Row | dict) -> dict:
    event = dict(row)
    event["path"] = json.loads(event.pop("path_json", "[]") or "[]")
    return event


def _snapshot(day: str, db: sqlite3.Connection | None = None) -> dict | None:
    if db is None:
        with _read_connect() as owned:
            return _snapshot(day, owned)
    row = db.execute(
        "SELECT payload_json FROM pattern_day_snapshots WHERE trade_date=? AND algorithm_version=?",
        (day, ALGORITHM_VERSION),
    ).fetchone()
    return json.loads(row[0]) if row else None


def _available_dates() -> list[str]:
    with _read_connect() as db:
        return [row[0] for row in db.execute(
            "SELECT trade_date FROM pattern_day_snapshots WHERE coverage='full' AND algorithm_version=? "
            "ORDER BY trade_date DESC LIMIT ?", (ALGORITHM_VERSION, HISTORY_SESSIONS),
        )][::-1]


def _event_groups(items: list[dict], live: bool) -> list[dict]:
    sealed_label = "当前封板" if live else "连板进行中"
    broken_label = "临时断板" if live else "确认断板"
    groups = []
    for status, label in (("sealed", sealed_label), (("temporary_break" if live else "confirmed_break"), broken_label)):
        rows = [item for item in items if item["status"] == status]
        if rows:
            groups.append({"key": status, "label": label, "count": len(rows), "items": sorted(rows, key=lambda item: (-item["board_height"], item["code"]))})
    return groups


def _intraday_window(now: datetime | None = None, *, quote_day: str | None = None,
                     require_current_quote: bool = False) -> dict:
    """QuickTiny-compatible intraday gate: trading days, 09:25–15:00 only."""
    current = now or china_now()
    today = current.strftime("%Y-%m-%d")
    minute = current.hour * 60 + current.minute
    available = current.weekday() < 5 and 9 * 60 + 25 <= minute < 15 * 60
    if current.weekday() >= 5:
        reason = "非交易日不开放盘中预演"
    elif minute < 9 * 60 + 25:
        reason = "盘中预演于交易日 09:25 开放"
    elif minute >= 15 * 60:
        reason = "盘中预演已于 15:00 结束，请使用收盘定稿"
    else:
        reason = ""
    if available and require_current_quote and quote_day != today:
        available = False
        reason = "09:25 后等待当日实时行情发布，请稍后刷新"
    return {
        "available": available, "date": today, "opens_at": "09:25", "closes_at": "15:00",
        "quote_day": quote_day, "reason": reason,
    }


def _complete_live_paths(items: list[dict]) -> list[dict]:
    """Attach the completed historical board path to in-memory live events.

    Live pool rows never pass through ``_store_day`` (by design), so without
    this step their path remains empty and V1 cannot match any v3 snapshot.
    Do not persist the hypothetical intraday state; only enrich the response.
    """
    completed = []
    with _read_connect() as db:
        for item in items:
            event = dict(item)
            height = int(event.get("board_height") or 0)
            if event.get("status") == "sealed" and height > 1:
                previous_day = trade_calendar.prev_trade_date(event["event_date"])
                prior_event = {
                    **event, "event_date": previous_day, "board_height": height - 1,
                    "status": "sealed",
                } if previous_day else None
                prefix = _path_from_daily_bars(db, prior_event) if prior_event else []
                path = [*prefix, event["board_type"]] if len(prefix) == height - 1 else _path_for(db, event)
            else:
                path = _path_for(db, event)
            complete = len(path) == height and all(node != "断板" for node in path)
            if complete:
                event["path"] = path
            event["matching_eligible"] = complete
            event["matching_note"] = "" if complete else "盘中逐板路径尚未完整，暂不可计算历史匹配"
            completed.append(event)
    return completed


def _events_response(day: str, items: list[dict], *, live: bool, coverage: str,
                     source_hits: dict[str, int], note: str = "",
                     intraday_window: dict | None = None) -> dict:
    sync = status()
    full_dates = _available_dates()
    sync_quality = sync.get("quality") or {}
    ready_count = int(sync_quality.get("snapshot_ready_dates") or (
        len(full_dates) if not sync_quality else 0
    ))
    corpus_ready = (
        ready_count >= HISTORY_SESSIONS
        and int(sync_quality.get("required_dates") or HISTORY_SESSIONS) == HISTORY_SESSIONS
        and int(sync_quality.get("unknown_gap_count") or 0) == 0
    )
    matching = coverage == "full" and corpus_ready and (live or day in full_dates)
    return {
        "available": bool(items), "coverage": coverage, "matching_available": matching,
        "date": day, "mode": "intraday" if live else "close", "anchor_time": _now(),
        "algorithm_version": ALGORITHM_VERSION, "events": items, "groups": _event_groups(items, live),
        "available_dates": full_dates, "note": note,
        "intraday_window": intraday_window or _intraday_window(),
        "quality": {"event_count": len(items), "source_hits": source_hits,
                    "history_sessions": int(sync_quality.get("snapshot_ready_dates") or 0),
                    "required_sessions": HISTORY_SESSIONS,
                    **sync_quality},
        "sync": sync,
    }


def events(mode: str = "close", trade_date: str | None = None) -> dict:
    if mode not in {"close", "intraday"}:
        raise ValueError("mode 只能是 close 或 intraday")
    live = mode == "intraday"
    if live:
        window = _intraday_window()
        day = window["date"]
        if trade_date and validate_trade_date(trade_date) != day:
            window = {**window, "available": False, "reason": "盘中预演只支持当前交易日"}
        if not window["available"]:
            return _events_response(
                day, [], live=True, coverage="unavailable", source_hits={},
                note=window["reason"], intraday_window=window,
            )
        quote_day = trade_calendar.quote_trade_day()
        window = _intraday_window(quote_day=quote_day, require_current_quote=True)
        if not window["available"]:
            return _events_response(
                day, [], live=True, coverage="unavailable", source_hits={},
                note=window["reason"], intraday_window=window,
            )
        return _live_events(day)
    day = validate_trade_date(trade_date) if trade_date else trade_calendar.latest_session()
    if not day:
        raise RuntimeError("暂时无法确认交易日")
    snap = _snapshot(day)
    if snap:
        return _events_response(day, snap["events"], live=False, coverage=snap["coverage"],
                                source_hits=snap.get("source_hits") or {})
    try:
        current = _rebuild_from_daily_bars(day)
        hits = {"local_full_daily_rebuild": len(current)}
        _store_day(day, current, hits)
    except Exception as exc:
        request_sync(day)
        return _events_response(day, [], live=False, coverage="building", source_hits={},
                                note=f"事件池与一年历史库正在构建：{type(exc).__name__}: {exc}")
    request_sync(day)
    return _events_response(day, current, live=False, coverage="building", source_hits=hits,
                            note="当前事件池已可查看；一年历史库完整前暂不开放匹配统计。")


def _live_events(day: str) -> dict:
    key = day
    now = time.monotonic()
    if _LIVE_CACHE.get("key") == key and now < float(_LIVE_CACHE.get("until") or 0):
        return _LIVE_CACHE["payload"]  # type: ignore[return-value]
    with _LIVE_LOCK:
        now = time.monotonic()
        if _LIVE_CACHE.get("key") == key and now < float(_LIVE_CACHE.get("until") or 0):
            return _LIVE_CACHE["payload"]  # type: ignore[return-value]
        try:
            items, hits = _fetch_live_events(day)
        except Exception as exc:
            window = _intraday_window(quote_day=day, require_current_quote=True)
            return _events_response(
                day, [], live=True, coverage="unavailable", source_hits={},
                note=f"盘中事件池暂不可用：{type(exc).__name__}: {exc}",
                intraday_window=window,
            )
        items = _complete_live_paths(items)
        sync = status()
        quality = sync.get("quality") or {}
        ready_count = int(quality.get("snapshot_ready_dates") or (
            len(_available_dates()) if not quality else 0
        ))
        ready = (ready_count >= HISTORY_SESSIONS
                 and sync.get("state") in {"ready", "complete", "idle"})
        payload = _events_response(
            day, items, live=True, coverage="full" if ready else "building",
            source_hits=hits,
            note="盘中以最新价假设收盘；高低点、封板状态和量能仍会变化。",
            intraday_window=_intraday_window(quote_day=day, require_current_quote=True),
        )
        _LIVE_CACHE.clear()
        _LIVE_CACHE.update({"key": key, "until": now + LIVE_CACHE_SECONDS, "payload": payload})
        return payload


def search(query: str, mode: str = "close", trade_date: str | None = None, limit: int = 10) -> list[dict]:
    needle = str(query or "").strip().lower()
    if not needle:
        return []
    payload = events(mode, trade_date)
    return [
        {"code": item["code"], "name": item["name"], "industry": item["industry"],
         "status": item["status"], "board_height": item["board_height"], "board_type": item["board_type"]}
        for item in payload["events"]
        if needle in item["code"].lower() or needle in item["name"].lower()
    ][:max(1, min(10, limit))]


def _bars(code: str, end: str, limit: int = 60, *, db: sqlite3.Connection | None = None,
          cache: dict[tuple[str, str, int], list[dict]] | None = None) -> list[dict]:
    key = (code, end, limit)
    if cache is not None and key in cache:
        return cache[key]
    if db is None:
        with _read_connect() as owned:
            return _bars(code, end, limit, db=owned, cache=cache)
    rows = [dict(row) for row in db.execute(
        "SELECT trade_date AS date,open,high,low,close,pct_chg,volume,amount,turnover_pct,trade_status "
        "FROM daily_bars WHERE code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?",
        (code, end, limit),
    )][::-1]
    if cache is not None:
        cache[key] = rows
    return rows


def _future_bars(code: str, day: str, count: int = 3, *, db: sqlite3.Connection | None = None,
                 cache: dict[tuple[str, str, int], list[dict]] | None = None) -> list[dict]:
    key = (code, day, count)
    if cache is not None and key in cache:
        return cache[key]
    if db is None:
        with _read_connect() as owned:
            return _future_bars(code, day, count, db=owned, cache=cache)
    rows = [dict(row) for row in db.execute(
        "SELECT trade_date AS date,open,high,low,close,pct_chg,volume,amount,turnover_pct,trade_status,is_st "
        "FROM daily_bars WHERE code=? AND trade_date>? AND trade_status=1 ORDER BY trade_date LIMIT ?",
        (code, day, count),
    )]
    if cache is not None:
        cache[key] = rows
    return rows


def _distance_score(pairs: Iterable[tuple[float | None, float | None]], scale: float = 1.0) -> float:
    distances = []
    for left, right in pairs:
        if left is None or right is None:
            continue
        denom = max(abs(left), abs(right), scale)
        distances.append(abs(left - right) / denom)
    return max(0.0, 100.0 * (1.0 - min(1.0, statistics.fmean(distances) if distances else 1.0)))


def _shape_score(target: dict, candidate: dict, *, db: sqlite3.Connection | None = None,
                 bars_cache: dict[tuple[str, str, int], list[dict]] | None = None) -> float:
    left = _bars(target["code"], target["event_date"], 20, db=db, cache=bars_cache)
    right = _bars(candidate["code"], candidate["event_date"], 20, db=db, cache=bars_cache)
    if len(left) < 10 or len(right) < 10:
        return 0.0
    size = min(len(left), len(right), 20)
    left, right = left[-size:], right[-size:]
    l0, r0 = left[0].get("close") or 1, right[0].get("close") or 1
    lp = [(row.get("close") or l0) / l0 for row in left]
    rp = [(row.get("close") or r0) / r0 for row in right]
    lv = [math.log1p(max(0.0, _number(row.get("volume"), 0.0))) for row in left]
    rv = [math.log1p(max(0.0, _number(row.get("volume"), 0.0))) for row in right]
    price = _distance_score(zip(lp, rp), 0.1)
    volume = _distance_score(zip(lv, rv), 1.0)
    return round(price * 0.75 + volume * 0.25, 1)


def _environment_for(day: str, *, db: sqlite3.Connection | None = None,
                     cache: dict[str, dict] | None = None) -> dict:
    if cache is not None and day in cache:
        return cache[day]
    value = (_snapshot(day, db) or {}).get("market_environment") or {}
    if cache is not None:
        cache[day] = value
    return value


def score_case(target: dict, candidate: dict, algorithm: str = "v1", *, intraday: bool = False,
               db: sqlite3.Connection | None = None,
               bars_cache: dict[tuple[str, str, int], list[dict]] | None = None,
               environment_cache: dict[str, dict] | None = None) -> dict:
    identity = 100.0
    path_suffix = same_path_suffix(target.get("path") or [], candidate.get("path") or [])
    path_score = min(100.0, 70.0 + 30.0 * path_suffix / max(1, int(target["board_height"])))
    if algorithm == "v2":
        volume_price = _shape_score(target, candidate, db=db, bars_cache=bars_cache)
    else:
        volume_price = _distance_score([
            (_number(target.get("pct_chg")), _number(candidate.get("pct_chg"))),
            (_number(target.get("turnover_pct")), _number(candidate.get("turnover_pct"))),
            (_number(target.get("amount")), _number(candidate.get("amount"))),
            (_number(target.get("open")), _number(candidate.get("open"))),
        ])
    liquidity = _distance_score([
        (math.log1p(max(0.0, _number(target.get("amount"), 0.0))), math.log1p(max(0.0, _number(candidate.get("amount"), 0.0)))),
        (_number(target.get("turnover_pct")), _number(candidate.get("turnover_pct"))),
    ])
    if db is None and environment_cache is None:
        te, ce = _environment_for(target["event_date"]), _environment_for(candidate["event_date"])
    else:
        te = _environment_for(target["event_date"], db=db, cache=environment_cache)
        ce = _environment_for(candidate["event_date"], db=db, cache=environment_cache)
    environment = _distance_score([
        (_number(te.get("limit_up_count")), _number(ce.get("limit_up_count"))),
        (_number(te.get("break_rate")), _number(ce.get("break_rate"))),
        (_number(te.get("max_height")), _number(ce.get("max_height"))),
        (_number(te.get("promotion_rate")), _number(ce.get("promotion_rate"))),
        (_number(te.get("index_pct")), _number(ce.get("index_pct"))),
    ])
    if intraday:
        volume_price = 50 + (volume_price - 50) * 0.5
        liquidity = 50 + (liquidity - 50) * 0.5
    total = identity * 0.20 + path_score * 0.30 + volume_price * 0.30 + liquidity * 0.10 + environment * 0.10
    return {
        "identity": round(identity, 1), "path": round(path_score, 1),
        "volume_price": round(volume_price, 1), "liquidity": round(liquidity, 1),
        "environment": round(environment, 1), "total": round(total, 1),
        "path_suffix": path_suffix, "exact_path": (target.get("path") or []) == (candidate.get("path") or []),
    }


def _returns(event: dict, future: list[dict]) -> dict:
    base = _number(event.get("close"))
    if not base or not future:
        return {"t1": None, "t3": None, "t1_high": None, "t1_pct": None}
    t1 = future[0]
    last = future[min(2, len(future) - 1)]
    return {
        "t1": round((t1["close"] / base - 1) * 100, 2) if t1.get("close") else None,
        "t3": round((last["close"] / base - 1) * 100, 2) if last.get("close") else None,
        "t1_high": round((t1["high"] / base - 1) * 100, 2) if t1.get("high") else None,
        "t1_pct": _number(t1.get("pct_chg")),
    }


def outcome_label(result: dict, code: str = "", is_st: bool = False) -> str:
    t1, high = result.get("t1"), result.get("t1_high")
    if t1 is None:
        return "暂无验证"
    limit_pct = market_limit_pct(code, is_st)
    if t1 >= limit_pct - 0.3:
        return "强修复/晋级"
    if high is not None and high >= max(5.0, limit_pct * 0.65):
        return "冲高修复"
    if t1 >= 0:
        return "温和修复"
    if t1 <= -limit_pct + 0.3:
        return "继续被核"
    return "弱势延续"


def _case_payload(event: dict, scores: dict, future: list[dict], *, db: sqlite3.Connection | None = None,
                  bars_cache: dict[tuple[str, str, int], list[dict]] | None = None) -> dict:
    result = _returns(event, future)
    chart = _bars(event["code"], event["event_date"], 26, db=db, cache=bars_cache) + future[:3]
    return {
        "code": event["code"], "name": event["name"], "event_date": event["event_date"],
        "industry": event.get("industry") or "未知", "theme": event.get("theme") or "",
        "status": event["status"], "board_height": event["board_height"],
        "board_type": event["board_type"], "path": event.get("path") or [],
        "scores": scores, "result": {**result, "label": outcome_label(result, event["code"])},
        "chart": chart,
    }


def _stats(cases: list[dict], complete_count: int) -> dict:
    sample = cases[:STAT_SAMPLE]
    t1_with_limits = [
        (case["result"]["t1"], market_limit_pct(case["code"]))
        for case in sample
        if case["result"]["t1"] is not None
    ]
    t1 = [value for value, _ in t1_with_limits]
    highs = [case["result"]["t1_high"] for case in sample if case["result"]["t1_high"] is not None]
    return {
        "sample_count": len(sample), "complete_path_count": complete_count,
        "next_red_rate": round(sum(value > 0 for value in t1) / len(t1) * 100, 1) if t1 else None,
        "next_avg_high": round(statistics.fmean(highs), 2) if highs else None,
        "next_close_median": round(statistics.median(t1), 2) if t1 else None,
        "next_close_mean": round(statistics.fmean(t1), 2) if t1 else None,
        "next_limit_up": sum(value >= limit - 0.3 for value, limit in t1_with_limits),
        "next_limit_down": sum(value <= -limit + 0.3 for value, limit in t1_with_limits),
    }


def verification_scripts(status_value: str) -> list[dict]:
    if status_value == "sealed":
        return [
            {"title": "继续晋级", "detail": "次日竞价与同身位竞争中保持主动，分歧后能够回封。"},
            {"title": "分歧承接", "detail": "开板或回落后承接有效，题材核心未同步走弱。"},
            {"title": "晋级失败", "detail": "同身位被卡位、题材退潮或放量后无法重新封板。"},
        ]
    return [
        {"title": "强修复", "detail": "次日不再接近跌停开盘，盘中收回断板日收盘并出现主动攻击。"},
        {"title": "独立反抽", "detail": "个股冲高但题材扩散偏弱，只按高标反抽观察。"},
        {"title": "继续退潮", "detail": "继续弱开且无法收回断板日收盘，或同高度高标继续扩散负反馈。"},
    ]


def _target_event(code: str, mode: str, day: str) -> tuple[dict, dict]:
    directory = events(mode, day)
    target = next((item for item in directory["events"] if item["code"] == code), None)
    if not target:
        raise ValueError("该股票不在当前事件池中，不能计算")
    return target, directory


def calculate(code: str, mode: str = "close", algorithm: str = "v1", trade_date: str | None = None) -> dict:
    code = str(code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("code 必须是 6 位股票代码")
    if mode not in {"close", "intraday"} or algorithm not in {"v1", "v2"}:
        raise ValueError("mode/algorithm 参数无效")
    if mode == "intraday":
        window = _intraday_window()
        day = window["date"]
        if trade_date and validate_trade_date(trade_date) != day:
            raise RuntimeError("盘中预演只支持当前交易日")
        if not window["available"]:
            raise RuntimeError(window["reason"])
        quote_day = trade_calendar.quote_trade_day()
        window = _intraday_window(quote_day=quote_day, require_current_quote=True)
        if not window["available"]:
            raise RuntimeError(window["reason"])
    else:
        day = validate_trade_date(trade_date) if trade_date else trade_calendar.latest_session()
    if not day:
        raise RuntimeError("暂时无法确认交易日")
    if mode == "close":
        with _read_connect() as db:
            cached = db.execute(
                "SELECT payload_json FROM pattern_match_cache WHERE trade_date=? AND code=? AND algorithm=? AND algorithm_version=?",
                (day, code, algorithm, ALGORITHM_VERSION),
            ).fetchone()
        if cached:
            return json.loads(cached[0])
    target, directory = _target_event(code, mode, day)
    if len(target.get("path") or []) != int(target.get("board_height") or 0):
        raise RuntimeError("该标的盘中逐板路径尚未完整，暂不可计算历史匹配")
    if not directory["matching_available"]:
        raise RuntimeError("一年历史库尚未完成完整性验证；事件池可查看，历史匹配暂不可用")
    bars_cache: dict[tuple[str, str, int], list[dict]] = {}
    future_cache: dict[tuple[str, str, int], list[dict]] = {}
    environment_cache: dict[str, dict] = {}
    with _read_connect() as db:
        rows = [_row_event(row) for row in db.execute(
            "SELECT * FROM pattern_limit_events WHERE event_date<? AND status=? AND board_height=? "
            "ORDER BY event_date DESC LIMIT 1600", (day, target["status"], target["board_height"]),
        )]
        candidates = []
        target_tail = (target.get("path") or [])[-2:]
        for event in rows:
            if algorithm == "v1" and (event.get("path") or [])[-2:] != target_tail:
                continue
            future = _future_bars(event["code"], event["event_date"], 3, db=db, cache=future_cache)
            if len(future) < 3:
                continue
            scores = score_case(
                target, event, algorithm, intraday=mode == "intraday", db=db,
                bars_cache=bars_cache, environment_cache=environment_cache,
            )
            candidates.append((event, scores, future))
        eligible_count = len(candidates)
        candidates = candidates[:MAX_CANDIDATES]
        if algorithm == "v1":
            candidates.sort(key=lambda item: (
                not item[1]["exact_path"], -item[1]["path_suffix"], -item[1]["volume_price"],
                -int(item[0]["event_date"].replace("-", "")),
            ))
        else:
            candidates.sort(key=lambda item: (-item[1]["volume_price"], -int(item[0]["event_date"].replace("-", ""))))
        all_cases = [
            _case_payload(*item, db=db, bars_cache=bars_cache)
            for item in candidates[:STAT_SAMPLE]
        ]
        complete_count = sum(case["scores"]["exact_path"] for case in all_cases)
        target_chart = _bars(code, day, 42, db=db, cache=bars_cache)
    payload = {
        "available": True, "coverage": directory["coverage"], "matching_available": True,
        "date": day, "mode": mode, "anchor_time": directory["anchor_time"],
        "algorithm": algorithm, "algorithm_version": ALGORITHM_VERSION,
        "target": {**target, "chart": target_chart}, "stats": _stats(all_cases, complete_count),
        "cases": all_cases[:VISIBLE_CASES], "candidate_count": len(candidates),
        "scripts": verification_scripts(target["status"]),
        "warnings": ([
            "盘中预演按当前价格假设收盘，当日高低点和封板状态仍可能继续变化。",
            "当日成交量与换手率尚未完成，相关量价因子已自动降权 50%。",
        ] if mode == "intraday" else []) + ([
            f"候选池超过 {MAX_CANDIDATES} 个，仅保留最近案例。"
        ] if eligible_count > MAX_CANDIDATES else []),
        "quality": directory["quality"],
        "disclaimer": "历史相似度与历史结果不代表未来成功率，不构成买卖建议。",
    }
    if mode == "close":
        with _transaction() as db:
            db.execute(
                "INSERT INTO pattern_match_cache VALUES(?,?,?,?,?,?) ON CONFLICT DO UPDATE SET "
                "payload_json=excluded.payload_json,generated_at=excluded.generated_at",
                (day, code, algorithm, ALGORITHM_VERSION, json.dumps(payload, ensure_ascii=False), _now()),
            )
    return payload


def _new_run(day: str | None) -> str:
    run_id = uuid.uuid4().hex
    with _transaction() as db:
        db.execute("INSERT INTO pattern_sync_runs(run_id,requested_date,state,phase,started_at,updated_at) VALUES(?,?,?,?,?,?)",
                   (run_id, day, "queued", "queued", _now(), _now()))
    return run_id


def _update_run(run_id: str, **changes) -> None:
    fields, values = [], []
    mapping = {"missing": "missing_json", "source_hits": "source_hits_json"}
    for key, value in changes.items():
        if value is None:
            continue
        field = mapping.get(key, key)
        if key in mapping:
            value = json.dumps(value, ensure_ascii=False)
        fields.append(f"{field}=?")
        values.append(value)
    if fields:
        fields.append("updated_at=?")
        values.append(_now())
        with _transaction() as db:
            db.execute(f"UPDATE pattern_sync_runs SET {','.join(fields)} WHERE run_id=?", (*values, run_id))


def _record_failure(run_id: str, trade_date: str, phase: str, source: str,
                    exc: BaseException | str, *, retriable: bool,
                    category: str = "provider_network") -> None:
    message = str(exc)
    error_type = type(exc).__name__ if isinstance(exc, BaseException) else "SyncFailure"
    with _transaction() as db:
        db.execute(
            "INSERT INTO pattern_sync_failures(run_id,trade_date,phase,source,error_type,category,message,retriable,attempts,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,trade_date,phase,source) DO UPDATE SET "
            "error_type=excluded.error_type,category=excluded.category,message=excluded.message,retriable=excluded.retriable,"
            "attempts=pattern_sync_failures.attempts+1,created_at=excluded.created_at",
            (run_id, trade_date or "", phase, source, error_type, category,
             message[:1000], int(retriable), 1, _now()),
        )


def _quality_for(db: sqlite3.Connection, end: str | None, source_hits: dict) -> dict:
    required_corpus = HISTORY_SESSIONS + PRELUDE_SESSIONS
    params: tuple = (end, required_corpus) if end else (required_corpus,)
    predicate = "WHERE trade_date<=?" if end else ""
    rows = [dict(row) for row in db.execute(
        "SELECT * FROM (SELECT * FROM corpus_date_coverage " + predicate +
        " ORDER BY trade_date DESC LIMIT ?) ORDER BY trade_date",
        params,
    )]
    corpus_dates = [row["trade_date"] for row in rows]
    target_dates = corpus_dates[-HISTORY_SESSIONS:]
    if not rows:
        snapshot_ready = int(db.execute(
            "SELECT COUNT(*) FROM (SELECT trade_date FROM pattern_day_snapshots "
            "WHERE coverage='full' AND algorithm_version=? "
            "AND (? IS NULL OR trade_date<=?) ORDER BY trade_date DESC LIMIT ?)",
            (ALGORITHM_VERSION, end, end, HISTORY_SESSIONS),
        ).fetchone()[0])
        return {
            "required_dates": HISTORY_SESSIONS, "corpus_required_dates": required_corpus,
            "universe_ready_dates": 0, "bars_ready_dates": 0, "snapshot_ready_dates": snapshot_ready,
            "unknown_gap_count": 0, "source_hits": source_hits,
            "canonical_instrument_count": 0, "invalid_directory_count": 0,
            "alias_merged_count": 0, "lifecycle_excluded_count": 0,
            "algorithm_version": ALGORITHM_VERSION, "updated_at": None,
        }
    snapshot_ready = 0
    if target_dates:
        target_marks = ",".join("?" for _ in target_dates)
        snapshot_ready = int(db.execute(
            f"SELECT COUNT(*) FROM pattern_day_snapshots WHERE trade_date IN ({target_marks}) "
            "AND coverage='full' AND algorithm_version=?",
            (*target_dates, ALGORITHM_VERSION),
        ).fetchone()[0])
    bars_ready = sum(bool(row["universe_count"]) and not int(row["unknown_count"]) for row in rows)
    canonical_count = int(db.execute(
        "SELECT COUNT(*) FROM instrument_master WHERE security_type='stock'"
    ).fetchone()[0])
    return {
        "required_dates": HISTORY_SESSIONS, "corpus_required_dates": required_corpus,
        "universe_ready_dates": sum(bool(row["universe_count"]) for row in rows),
        "bars_ready_dates": bars_ready, "snapshot_ready_dates": snapshot_ready,
        "unknown_gap_count": sum(int(row["unknown_count"]) for row in rows),
        "canonical_instrument_count": canonical_count,
        "invalid_directory_count": sum(int(row["invalid_directory_count"]) for row in rows),
        "alias_merged_count": max((int(row["alias_merged_count"]) for row in rows), default=0),
        "lifecycle_excluded_count": sum(int(row["lifecycle_excluded_count"]) for row in rows),
        "source_hits": source_hits, "algorithm_version": ALGORITHM_VERSION,
        "updated_at": max((row["updated_at"] for row in rows), default=None),
    }


def _failure_summary(db: sqlite3.Connection, run_id: str | None) -> dict:
    if not run_id:
        return {"retriable_count": 0, "deterministic_count": 0, "categories": {}, "samples": []}
    counts = {bool(row[0]): int(row[1]) for row in db.execute(
        "SELECT retriable,COUNT(*) FROM pattern_sync_failures WHERE run_id=? GROUP BY retriable", (run_id,),
    )}
    categories = {row[0]: int(row[1]) for row in db.execute(
        "SELECT category,COUNT(*) FROM pattern_sync_failures WHERE run_id=? GROUP BY category", (run_id,),
    )}
    samples = [dict(row) for row in db.execute(
        "SELECT trade_date,phase,source,error_type,category,message,retriable,attempts "
        "FROM pattern_sync_failures WHERE run_id=? ORDER BY created_at DESC LIMIT 20", (run_id,),
    )]
    return {"retriable_count": counts.get(True, 0), "deterministic_count": counts.get(False, 0),
            "categories": categories, "samples": samples}


def status() -> dict:
    try:
        with _read_connect() as db:
            row = db.execute("SELECT * FROM pattern_sync_runs ORDER BY started_at DESC LIMIT 1").fetchone()
            dates = [item[0] for item in db.execute(
                "SELECT trade_date FROM pattern_day_snapshots WHERE coverage='full' AND algorithm_version=? "
                "ORDER BY trade_date DESC LIMIT ?", (ALGORITHM_VERSION, HISTORY_SESSIONS),
            )][::-1]
            source_hits = json.loads(row["source_hits_json"] or "{}") if row else {}
            quality = _quality_for(db, row["requested_date"] if row else None, source_hits)
            failures = _failure_summary(db, row["run_id"] if row else None)
    except sqlite3.DatabaseError as exc:
        return {"state": "failed", "phase": "database", "completed": 0, "total": 0, "progress": 0.0,
                "available_dates": [], "quality": {"required_dates": HISTORY_SESSIONS,
                "algorithm_version": ALGORITHM_VERSION}, "failure_summary": {"retriable_count": 0,
                "deterministic_count": 1, "samples": []}, "error": f"剧本研究数据库损坏：{exc}"}
    if not row:
        return {"state": "idle", "phase": "idle", "completed": 0, "total": HISTORY_SESSIONS,
                "progress": 0.0, "available_dates": dates, "latest_success_date": dates[-1] if dates else None,
                "quality": quality, "failure_summary": failures}
    result = dict(row)
    total, completed = int(result.get("total") or 0), int(result.get("completed") or 0)
    result.update({
        "progress": round(completed / total * 100, 1) if total else 0.0,
        "missing": json.loads(result.pop("missing_json") or "[]"),
        "source_hits": json.loads(result.pop("source_hits_json") or "{}"),
        "available_dates": dates, "latest_success_date": dates[-1] if dates else None,
        "quality": quality, "failure_summary": failures,
    })
    return result


def _session_dates(end: str | None) -> list[str]:
    end = end or trade_calendar.latest_session()
    if not end:
        raise RuntimeError("暂时无法确认最近交易日")
    dates = trade_calendar.trade_dates_ending_at(end, HISTORY_SESSIONS + PRELUDE_SESSIONS)
    if end not in dates:
        raise RuntimeError(f"{end} 不是可确认的已收盘交易日")
    return dates


@contextmanager
def _process_lock():
    lock = FileLock(growth_ladders._db_path().with_suffix(".pattern.sync.lock"))
    if not lock.acquire(blocking=False):
        raise RuntimeError("另一个服务进程正在同步剧本研究")
    try:
        yield
    finally:
        lock.release()


def _sync_once(requested: str | None) -> None:
    with _RUN_LOCK, _process_lock():
        current = status()
        run_id = current.get("run_id") if current.get("state") in {"queued", "running"} else _new_run(requested)
        try:
            dates = _session_dates(requested)
            targets = dates[-HISTORY_SESSIONS:]
            missing, hits = [], Counter()

            def corpus_progress(update: dict) -> None:
                _update_run(
                    run_id, state="running", phase=update["phase"],
                    completed=int(update.get("completed") or 0), total=int(update.get("total") or 0), error="",
                )

            _update_run(run_id, state="running", phase="directory_repair", completed=0,
                        total=len(dates), error="", finished_at="")
            corpus = growth_ladders.ensure_corpus_window(dates, corpus_progress)
            hits.update(corpus.get("source_hits") or {})
            missing = list(corpus.get("missing_dates") or [])
            if missing:
                provider_failures = corpus.get("fetch_failures") or []
                any_retriable = False
                for day in missing:
                    coverage = (corpus.get("coverage") or {}).get(day) or {}
                    unknown_codes = set(coverage.get("unknown_codes") or [])
                    related = [item for item in provider_failures if item.get("code") in unknown_codes]
                    retriable = any(bool(item.get("retriable")) for item in related)
                    any_retriable = any_retriable or retriable
                    category = "provider_network" if retriable else (
                        "provider_unsupported" if related else "lifecycle_unknown"
                    )
                    _record_failure(
                        run_id, day, "corpus_bars", "local_market_corpus",
                        f"未知日线缺口 {coverage.get('unknown_count', 0)} 个证券",
                        retriable=retriable, category=category,
                    )
                _update_run(run_id, missing=missing, source_hits=dict(hits))
                raise SyncFailure(
                    f"历史语料仍有 {len(missing)} 个日期存在未知缺口",
                    retriable=any_retriable,
                )

            _update_run(run_id, state="running", phase="events", completed=0, total=len(dates),
                        missing=[], source_hits=dict(hits), error="")
            for index, day in enumerate(dates, 1):
                snap = _snapshot(day)
                if snap and snap.get("coverage") == "full":
                    hits["local_snapshot"] += 1
                else:
                    try:
                        day_events = _rebuild_from_daily_bars(day)
                        source_hits = {"local_full_daily_rebuild": len(day_events)}
                        _store_day(
                            day, day_events, source_hits, coverage="full",
                            previous_day=dates[index - 2] if index > 1 else "",
                        )
                        hits["local_full_daily_rebuild"] += len(day_events)
                        hits["local_rebuilt_dates"] += 1
                    except Exception as exc:
                        missing.append(day)
                        _record_failure(run_id, day, "events", "local_full_daily_rebuild", exc,
                                        retriable=False, category="directory_invalid")
                if index == len(dates) or index % 5 == 0:
                    _update_run(run_id, completed=index, total=len(dates), missing=missing, source_hits=dict(hits))

            _update_run(run_id, state="running", phase="verify", completed=0, total=len(targets),
                        missing=missing, source_hits=dict(hits))
            target_missing = []
            for index, day in enumerate(targets, 1):
                snap = _snapshot(day)
                if day in missing or not snap or snap.get("coverage") != "full":
                    target_missing.append(day)
                if index == len(targets) or index % 10 == 0:
                    _update_run(run_id, completed=index, total=len(targets), missing=target_missing,
                                source_hits=dict(hits))
            if target_missing:
                raise SyncFailure(f"近一年仍缺 {len(target_missing)} 个指定的 v2 完整事件日",
                                  retriable=False)
            _update_run(run_id, state="ready", phase="complete", completed=len(targets), total=len(targets),
                        missing=[], source_hits=dict(hits), error="", finished_at=_now())
        except Exception as exc:
            failed_phase = status().get("phase") or "failed"
            _update_run(run_id, state="failed", phase=failed_phase,
                        error=f"{type(exc).__name__}: {exc}"[:1000],
                        finished_at=_now())
            raise


def request_sync(trade_date: str | None = None) -> dict:
    day = validate_trade_date(trade_date) if trade_date else None
    start_worker()
    current = status()
    if current.get("state") in {"queued", "running"}:
        return current
    _new_run(day)
    try:
        _QUEUE.put_nowait(day)
    except queue.Full:
        pass
    return status()


def _worker_loop() -> None:
    global _retry_at, _retry_index, _retry_date
    while not _STOP.is_set():
        requested = None
        try:
            requested = _QUEUE.get(timeout=60)
        except queue.Empty:
            latest = trade_calendar.latest_session()
            if _retry_at and time.monotonic() >= _retry_at:
                requested = _retry_date or latest
            elif latest and latest not in _available_dates() and (china_now().hour, china_now().minute) >= (15, 10):
                requested = latest
                if status().get("state") not in {"queued", "running"}:
                    _new_run(requested)
            else:
                continue
        if _STOP.is_set():
            break
        try:
            _sync_once(requested)
            _retry_at = 0.0
            _retry_index = 0
            _retry_date = None
        except Exception as exc:
            if getattr(exc, "retriable", True):
                delay = _RETRY_DELAYS[min(_retry_index, len(_RETRY_DELAYS) - 1)]
                _retry_index += 1
                _retry_at = time.monotonic() + delay
                _retry_date = requested
                print(f"⚠️ 剧本研究同步失败，{delay // 60} 分钟后重试：{type(exc).__name__}: {exc}")
            else:
                _retry_at = 0.0
                _retry_index = 0
                _retry_date = None
                print(f"⚠️ 剧本研究同步遇到确定性错误，已停止自动重试：{type(exc).__name__}: {exc}")


def _recover_interrupted_runs() -> None:
    """Turn crash-left running rows into an explicit resumable state."""
    lock = FileLock(growth_ladders._db_path().with_suffix(".pattern.sync.lock"))
    if not lock.acquire(blocking=False):
        return
    try:
        with _transaction() as db:
            db.execute(
                "UPDATE pattern_sync_runs SET state='failed',phase='interrupted',"
                "error='服务重启中断了上次同步；将从已完成语料继续',finished_at=? "
                "WHERE state IN ('queued','running')",
                (_now(),),
            )
    finally:
        lock.release()


def start_worker() -> None:
    global _THREAD
    with _WORKER_LOCK:
        if _THREAD and _THREAD.is_alive():
            return
        _recover_interrupted_runs()
        _STOP.clear()
        _THREAD = threading.Thread(target=_worker_loop, name="board-pattern-sync", daemon=True)
        _THREAD.start()
        current = status()
        quality = current.get("quality") or {}
        if (current.get("state") not in {"queued", "running"}
                and int(quality.get("snapshot_ready_dates") or 0) < HISTORY_SESSIONS):
            latest = trade_calendar.latest_session()
            if latest:
                _new_run(latest)
                try:
                    _QUEUE.put_nowait(latest)
                except queue.Full:
                    pass


def stop_worker() -> None:
    _STOP.set()
    try:
        _QUEUE.put_nowait(None)
    except queue.Full:
        pass


def reset_for_tests() -> None:
    global _THREAD, _retry_at, _retry_index, _retry_date
    stop_worker()
    if _THREAD and _THREAD.is_alive():
        _THREAD.join(timeout=1)
    _THREAD = None
    _retry_at = 0.0
    _retry_index = 0
    _retry_date = None
    _LIVE_CACHE.clear()
    while True:
        try:
            _QUEUE.get_nowait()
        except queue.Empty:
            break
