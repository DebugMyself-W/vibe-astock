"""Full-market, locally computed consecutive bullish-candle ladders.

The browser never builds a ladder from a partial remote ranking.  Remote
providers populate a durable SQLite corpus; only a completely covered session
is published as a snapshot.  iWencai is deliberately absent from membership
calculation and can remain an optional metadata enrichment elsewhere.
"""

from __future__ import annotations

import json
import math
import os
import queue
import re
import socket
import sqlite3
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from net_utils import MarketSession

from .followup_store import FileLock
from .paths import data_path
from .util import china_now, validate_trade_date


SCHEMA_VERSION = 3
ALGORITHM_VERSION = "growth-ladder-v2"
VISIBLE_SESSIONS = 20
WARMUP_SESSIONS = 160
RULE_TEXT = (
    "收盘连涨或连续收阳满 3 天进入天梯，按两者中较长的分层；"
    "开盘等于收盘且当日不跌也算收阳。收盘创近 120 日新高的单独标出；"
    "昨天不少于 5 天、今天断掉的标成断阳，停牌不算断阳。"
)

_DB_LOCK = threading.RLock()
_SYNC_QUEUE: queue.Queue[str | None] = queue.Queue(maxsize=1)
_WORKER_STOP = threading.Event()
_WORKER_THREAD: threading.Thread | None = None
_WORKER_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()
_RETRY_DELAYS = (300, 900, 1800)
_retry_at = 0.0
_retry_count = 0
_retry_requested: str | None = None
_LIVE_CACHE_SECONDS = 15.0
_LIVE_CACHE_LOCK = threading.Lock()
_live_cache: dict[str, object] = {}
_MIGRATION_LOCK = threading.Lock()
_MIGRATED_PATHS: set[str] = set()
_FETCH_CONTEXT = threading.local()


def _db_path() -> Path:
    override = os.environ.get("GROWTH_LADDERS_DB", "").strip()
    return Path(override).expanduser() if override else Path(data_path("cache", "growth_ladders.sqlite"))


def _now_text() -> str:
    return china_now().isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    key = str(path.resolve())
    if key not in _MIGRATED_PATHS:
        with _MIGRATION_LOCK:
            if key not in _MIGRATED_PATHS:
                _migrate(connection)
                _MIGRATED_PATHS.add(key)
    return connection


def _read_connect() -> sqlite3.Connection:
    """Open a short, query-only connection after the one-time startup migration."""
    path = _db_path()
    key = str(path.resolve())
    if key not in _MIGRATED_PATHS:
        bootstrap = _connect()
        bootstrap.close()
    connection = sqlite3.connect(path, timeout=2, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=1500")
    return connection


def _migrate(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS session_instruments (
            trade_date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            market TEXT NOT NULL,
            industry TEXT NOT NULL DEFAULT '未知',
            industry_source TEXT NOT NULL DEFAULT '',
            trade_status INTEGER NOT NULL DEFAULT 1,
            is_st INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (trade_date, code)
        );
        CREATE TABLE IF NOT EXISTS daily_bars (
            code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            pct_chg REAL,
            volume REAL,
            amount REAL,
            turnover_pct REAL,
            trade_status INTEGER NOT NULL DEFAULT 1,
            is_st INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (code, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_daily_bars_date ON daily_bars(trade_date);
        CREATE TABLE IF NOT EXISTS ladder_snapshots (
            trade_date TEXT PRIMARY KEY,
            algorithm_version TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            universe_count INTEGER NOT NULL,
            covered_count INTEGER NOT NULL,
            generated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sync_runs (
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
            finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS instrument_master (
            canonical_code TEXT PRIMARY KEY,
            source_code TEXT NOT NULL,
            exchange TEXT NOT NULL,
            security_type TEXT NOT NULL DEFAULT 'stock',
            name TEXT NOT NULL,
            listing_date TEXT,
            delisting_date TEXT,
            current_status TEXT NOT NULL DEFAULT 'unknown',
            aliases_json TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_instrument_master_exchange
            ON instrument_master(exchange, security_type, current_status);
        CREATE TABLE IF NOT EXISTS instrument_aliases (
            alias_code TEXT PRIMARY KEY,
            canonical_code TEXT NOT NULL,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(canonical_code) REFERENCES instrument_master(canonical_code)
        );
        CREATE TABLE IF NOT EXISTS corpus_date_coverage (
            trade_date TEXT PRIMARY KEY,
            universe_count INTEGER NOT NULL DEFAULT 0,
            covered_count INTEGER NOT NULL DEFAULT 0,
            suspended_count INTEGER NOT NULL DEFAULT 0,
            lifecycle_excluded_count INTEGER NOT NULL DEFAULT 0,
            unknown_count INTEGER NOT NULL DEFAULT 0,
            invalid_directory_count INTEGER NOT NULL DEFAULT 0,
            alias_merged_count INTEGER NOT NULL DEFAULT 0,
            source_hits_json TEXT NOT NULL DEFAULT '{}',
            algorithm_version TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS corpus_fetch_failures (
            canonical_code TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            provider TEXT NOT NULL,
            category TEXT NOT NULL,
            message TEXT NOT NULL,
            retriable INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(canonical_code,start_date,end_date,provider)
        );
        """
    )
    columns = {row[1] for row in db.execute("PRAGMA table_info(daily_bars)")}
    if "turnover_pct" not in columns:
        db.execute("ALTER TABLE daily_bars ADD COLUMN turnover_pct REAL")
    db.execute(
        "INSERT INTO metadata(key,value) VALUES('schema_version',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    db.commit()


@contextmanager
def _transaction():
    with _DB_LOCK:
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


def _a_share_code(code: str) -> bool:
    return bool(re.fullmatch(r"(?:60|68|00|30|43|83|87|92)\d{4}", code))


def _market(code: str) -> str:
    if code.startswith(("43", "83", "87", "92")):
        return "BJ"
    return "SH" if code.startswith(("60", "68")) else "SZ"


def _validated_stock_code(raw_code: str) -> tuple[str, str] | None:
    """Validate the provider exchange prefix before normalising the code.

    Baostock exposes indices, bonds and B shares alongside stocks.  Stripping
    ``sh.`` first turns Shanghai index ``sh.000300`` into an apparently valid
    Shenzhen equity.  Keep the raw prefix until the security type is proven.
    """
    raw = str(raw_code or "").strip().lower()
    if "." not in raw:
        return None
    prefix, value = raw.split(".", 1)
    code = value.zfill(6)
    if prefix == "sh" and re.fullmatch(r"(?:60|68)\d{4}", code):
        return code, "SH"
    if prefix == "sz" and re.fullmatch(r"(?:00|30)\d{4}", code):
        return code, "SZ"
    return None


def _defensive_non_stock_name(name: str) -> bool:
    value = str(name or "").upper()
    return any(token in value for token in ("指数", "债", "B股", "B 股"))


def _number(value, default=None):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _full_market_universe(*, include_inactive: bool = False) -> list[dict]:
    """Full A-share directory; optionally retain legacy inactive securities."""
    from . import fetchers

    rows = fetchers._clist(  # central market-network seam and host fallbacks live here
        fetchers.ALL_A_FS, "f12", "f2,f12,f13,f14,f18,f100", pz=100, max_pages=80,
    )
    result = {}
    for row in rows:
        code = str(row.get("f12") or "").zfill(6)
        if not _a_share_code(code):
            continue
        name = str(row.get("f14") or code).strip()
        if _defensive_non_stock_name(name):
            continue
        # Legacy Beijing codes are query aliases after the 920xxx migration,
        # never independent current listings.  Keeping both creates duplicate
        # securities and admits Tencent's frozen legacy quotations.
        if code.startswith(("43", "83", "87")):
            continue
        active = not ("f2" in row or "f18" in row) or bool(
            (_number(row.get("f2")) or 0) > 0 or (_number(row.get("f18")) or 0) > 0
        )
        if not include_inactive and not active:
            # ALL_A_FS contains the legacy delisting boards as well. Active
            # suspended shares retain a previous close (f18); permanently
            # delisted rows have neither current nor previous price.
            continue
        provider_market = str(row.get("f13") if row.get("f13") is not None else "")
        expected_market = "1" if code.startswith(("60", "68")) else "0"
        if provider_market and provider_market != expected_market:
            # ALL_A_FS also returns Shanghai indices whose six-digit code
            # collides with Shenzhen shares (000001, 000003, ...).
            continue
        result[code] = {
            "code": code,
            "name": name,
            "market": _market(code),
            "industry": str(row.get("f100") or "未知").strip() or "未知",
            "industry_source": "eastmoney_f100",
            "trade_status": 1,
            "is_st": int("ST" in str(row.get("f14") or "").upper()),
            "directory_active": int(active),
        }
    if not result:
        raise RuntimeError("全市场股票目录为空，拒绝创建不完整天梯")
    return list(result.values())


def _baostock_code(code: str) -> str:
    if code.startswith(("60", "68")):
        return f"sh.{code}"
    if code.startswith(("00", "30")):
        return f"sz.{code}"
    raise ValueError("Baostock 不支持北交所")


def _open_baostock():
    """Return a logged-in Baostock module, or ``None`` when unavailable."""
    try:
        import baostock as baostock_module
        login = baostock_module.login()
        return baostock_module if login.error_code == "0" else None
    except Exception:
        return None


def _result_rows(result) -> list[dict]:
    if result.error_code != "0":
        raise RuntimeError(f"Baostock 查询失败：{result.error_code} {result.error_msg}")
    rows = []
    while result.next():
        rows.append(dict(zip(result.fields, result.get_row_data())))
    return rows


def _baostock_session_universe(bs, trade_date: str) -> dict[str, dict]:
    rows = _result_rows(bs.query_all_stock(day=trade_date))
    result = {}
    for row in rows:
        raw = str(row.get("code") or "")
        validated = _validated_stock_code(raw)
        if not validated:
            continue
        code, market = validated
        name = str(row.get("code_name") or code).strip()
        if _defensive_non_stock_name(name):
            continue
        result[code] = {
            "code": code,
            "source_code": raw.lower(),
            "name": name,
            "market": market,
            "security_type": "stock",
            "trade_status": int(str(row.get("tradeStatus") or "1") == "1"),
        }
    return result


def _session_universe(bs, trade_date: str, current_universe: dict[str, dict]) -> tuple[dict[str, dict], str]:
    """Resolve a dated directory without making Baostock a publication gate.

    A previously persisted session directory is already the result of an
    earlier successful lookup and is therefore preferred.  On a fresh install
    Baostock remains the historical source, but a transient directory outage
    falls back to the current full-market directory.  Listing boundaries are
    subsequently pruned from the actual bar corpus.
    """
    stored = {item["code"]: item for item in _load_instruments(trade_date)}
    with _connect() as db:
        proven_bj = {row[0] for row in db.execute(
            "SELECT DISTINCT code FROM daily_bars WHERE code LIKE '92%' AND trade_date<=?",
            (trade_date,),
        )}

    def current_is_eligible(code: str, item: dict) -> bool:
        if item.get("market") != "BJ":
            return True
        return code.startswith("920") and item.get("directory_active") != 0 and code in proven_bj
    historical = {}
    if bs is not None:
        try:
            historical = _baostock_session_universe(bs, trade_date)
        except Exception:
            historical = {}
    if historical:
        merged = {}
        for code, item in historical.items():
            base = dict(stored.get(code) or current_universe.get(code) or {})
            base.update(item)
            base.setdefault("industry", "未知")
            base.setdefault("industry_source", "baostock_directory")
            merged[code] = base
        for code, item in current_universe.items():
            if item["market"] == "BJ" and current_is_eligible(code, item):
                merged[code] = dict(item)
        return merged, "baostock_directory"

    if stored:
        # An outage may reuse a prior directory, but only after applying the
        # same canonical stock rules.  This path deliberately does not add the
        # complete current directory to every historical date.
        result = {
            code: item for code, item in stored.items()
            if (not current_universe or code in current_universe)
            and current_is_eligible(code, current_universe.get(code, item))
            and ((item.get("market") == "SH" and code.startswith(("60", "68")))
                or (item.get("market") == "SZ" and code.startswith(("00", "30")))
                or (item.get("market") == "BJ" and code.startswith("92")))
            and not _defensive_non_stock_name(item.get("name") or "")
        }
        if result:
            return result, "local_snapshot"

    return {
        code: dict(item) for code, item in current_universe.items()
        if current_is_eligible(code, item)
    }, "current_directory_fallback"


def _filter_universe_by_recent_activity(
    current_universe: dict[str, dict], start_date: str, *, minimum_verified: int = 4000,
) -> dict[str, dict]:
    """Remove legacy/future directory entries once a mature local corpus exists.

    Eastmoney's broad A-share filters include hundreds of delisted, absorbed and
    pre-listing securities.  A mature full-market cache provides stronger dated
    evidence: every eligible security has either a traded or explicit suspended
    bar in the recent window.  On a fresh database we keep the remote directory
    intact so unknowns still block publication rather than being hidden.
    """
    with _connect() as db:
        verified = {row[0] for row in db.execute(
            "SELECT DISTINCT code FROM daily_bars WHERE trade_date>=?",
            (start_date,),
        )}
    if len(verified) < minimum_verified:
        return current_universe
    return {code: item for code, item in current_universe.items() if code in verified}


def _baostock_history(bs, code: str, start: str, end: str) -> list[dict]:
    fields = "date,open,high,low,close,volume,amount,pctChg,turn,tradestatus,isST"
    result = bs.query_history_k_data_plus(
        _baostock_code(code), fields, start_date=start, end_date=end,
        frequency="d", adjustflag="2",
    )
    rows = _result_rows(result)
    output = []
    for row in rows:
        status = int(str(row.get("tradestatus") or "0") == "1")
        output.append({
            "code": code, "trade_date": str(row.get("date") or "")[:10],
            "open": _number(row.get("open")), "high": _number(row.get("high")),
            "low": _number(row.get("low")), "close": _number(row.get("close")),
            "pct_chg": _number(row.get("pctChg")), "volume": _number(row.get("volume")),
            "amount": _number(row.get("amount")), "turnover_pct": _number(row.get("turn")),
            "trade_status": status,
            "is_st": int(str(row.get("isST") or "0") == "1"),
            "source": "baostock_qfq", "fetched_at": _now_text(),
        })
    return [row for row in output if row["trade_date"]]


_EASTMONEY_LOCK = threading.Lock()
_eastmoney_last_call = 0.0


def _eastmoney_history(code: str, start: str, end: str, limit: int = 400) -> list[dict]:
    """Serialized qfq history, primarily for the North Exchange."""
    global _eastmoney_last_call
    market = "1" if code.startswith(("60", "68")) else "0"
    params = {
        "secid": f"{market}.{code}", "klt": "101", "fqt": "1", "lmt": str(limit),
        "beg": start.replace("-", ""), "end": end.replace("-", ""),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    with _EASTMONEY_LOCK:
        wait = 1.05 - (time.monotonic() - _eastmoney_last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            with MarketSession() as session:
                response = session.get(
                    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                    params=params,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
                    timeout=15,
                )
                response.raise_for_status()
                lines = ((response.json().get("data") or {}).get("klines") or [])
        finally:
            _eastmoney_last_call = time.monotonic()
    output = []
    for line in lines:
        values = str(line).split(",")
        if len(values) < 9:
            continue
        output.append({
            "code": code, "trade_date": values[0], "open": _number(values[1]),
            "close": _number(values[2]), "high": _number(values[3]), "low": _number(values[4]),
            "volume": _number(values[5]), "amount": _number(values[6]),
            "pct_chg": _number(values[8]),
            "turnover_pct": _number(values[10]) if len(values) > 10 else None,
            "trade_status": 1, "is_st": 0,
            "source": "eastmoney_qfq", "fetched_at": _now_text(),
        })
    return output


def _ths_history(code: str, start: str, end: str) -> list[dict]:
    """同花顺前复权日线备份；响应是 JSONP，字段口径为日线 OHLCVA。"""
    url = f"https://d.10jqka.com.cn/v6/line/hs_{code}/01/last.js"
    with MarketSession() as session:
        response = session.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": f"https://stockpage.10jqka.com.cn/{code}/"},
            timeout=12,
        )
        response.raise_for_status()
        match = re.search(r"\((\{.*\})\)\s*;?\s*$", response.text, re.S)
        if not match:
            return []
        payload = json.loads(match.group(1))
    node = payload.get(f"hs_{code}") or payload.get(code) or payload
    raw = node.get("data") if isinstance(node, dict) else None
    if isinstance(raw, str):
        records = [item.split(",") for item in raw.split(";") if item]
    elif isinstance(raw, list):
        records = raw
    else:
        records = []
    output, previous_close = [], None
    for record in records:
        if not isinstance(record, (list, tuple)) or len(record) < 6:
            continue
        day = str(record[0])
        if len(day) == 8 and day.isdigit():
            day = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        if not (start <= day <= end):
            continue
        open_, high, low, close = map(_number, record[1:5])
        pct = ((close / previous_close - 1) * 100) if close and previous_close else None
        output.append({
            "code": code, "trade_date": day, "open": open_, "high": high, "low": low,
            "close": close, "volume": _number(record[5]),
            "amount": _number(record[6]) if len(record) > 6 else None, "pct_chg": pct,
            "trade_status": 1, "is_st": 0, "source": "ths_qfq", "fetched_at": _now_text(),
        })
        if close:
            previous_close = close
    return output


_TDX_SERVERS = (
    ("119.97.185.59", 7709), ("124.70.133.119", 7709),
    ("116.205.183.150", 7709), ("123.60.73.44", 7709),
    ("116.205.163.254", 7709), ("121.36.225.169", 7709),
    ("123.60.70.228", 7709), ("124.71.9.153", 7709),
    ("110.41.147.114", 7709), ("124.71.187.122", 7709),
)


def tdx_client():
    """Create a mootdx client only after a real one-bar validation."""
    try:
        from mootdx.quotes import Quotes
    except ImportError as exc:
        raise RuntimeError("mootdx 未安装") from exc
    for ip, port in _TDX_SERVERS:
        try:
            with socket.create_connection((ip, port), timeout=1.5):
                pass
            client = Quotes.factory(market="std", server=(ip, port))
            sample = client.bars(symbol="000001", frequency=9, offset=1)
            if sample is not None and not sample.empty:
                return client
        except Exception:
            continue
    for kwargs in ({"bestip": True}, {}):
        try:
            client = Quotes.factory(market="std", **kwargs)
            sample = client.bars(symbol="000001", frequency=9, offset=1)
            if sample is not None and not sample.empty:
                return client
        except Exception:
            continue
    raise RuntimeError("所有 mootdx 服务器均无法真实取到 K 线")


def _mootdx_history(code: str, limit: int = 400) -> list[dict]:
    frame = tdx_client().bars(symbol=code, frequency=9, offset=limit)
    if frame is None or frame.empty:
        return []
    rows = []
    previous_close = None
    for raw in sorted(frame.to_dict("records"), key=lambda item: str(item.get("datetime") or "")):
        close = _number(raw.get("close"))
        pct = ((close / previous_close - 1) * 100) if close and previous_close else None
        rows.append({
            "code": code, "trade_date": str(raw.get("datetime") or "")[:10],
            "open": _number(raw.get("open")), "high": _number(raw.get("high")),
            "low": _number(raw.get("low")), "close": close, "pct_chg": pct,
            "volume": _number(raw.get("vol")), "amount": _number(raw.get("amount")),
            "trade_status": 1, "is_st": 0, "source": "mootdx_raw",
            "fetched_at": _now_text(),
        })
        if close:
            previous_close = close
    return [row for row in rows if row["trade_date"]]


def _reaches_left_boundary(rows: list[dict]) -> bool:
    """Whether either published streak would be truncated by the first row."""
    ordered = sorted(rows, key=lambda row: row["trade_date"])
    if not ordered:
        return False
    positive_unbroken = True
    rising_unbroken = True
    for row in reversed(ordered):
        if not int(row.get("trade_status", 1)):
            positive_unbroken = rising_unbroken = False
            break
        open_, close, pct = _number(row.get("open")), _number(row.get("close")), _number(row.get("pct_chg"))
        positive = open_ is not None and close is not None and (
            close > open_ or (math.isclose(close, open_, abs_tol=1e-8) and pct is not None and pct >= 0)
        )
        positive_unbroken = positive_unbroken and positive
        rising_unbroken = rising_unbroken and pct is not None and pct > 0
        if not positive_unbroken and not rising_unbroken:
            return False
    return positive_unbroken or rising_unbroken


def _dated_fallback_history(bs, code: str, start: str, end: str, limit: int) -> list[dict]:
    outcome = _dated_fallback_history_result(bs, code, start, end, limit)
    _FETCH_CONTEXT.outcome = outcome
    return outcome["rows"]


def _dated_fallback_history_result(bs, code: str, start: str, end: str, limit: int) -> dict:
    failures = []

    def attempt(provider: str, loader):
        try:
            rows = loader() or []
            if not rows:
                failures.append({
                    "provider": provider, "category": "provider_unsupported",
                    "message": "数据源未返回该代码区间", "retriable": False,
                })
            return rows
        except Exception as exc:
            failures.append({
                "provider": provider, "category": "provider_network",
                "message": f"{type(exc).__name__}: {exc}"[:500], "retriable": True,
            })
            return []

    if _market(code) == "BJ":
        rows = attempt("eastmoney_history", lambda: _eastmoney_history(code, start, end, limit=limit))
        if not rows:
            rows = [row for row in attempt("mootdx_history", lambda: _mootdx_history(code, limit))
                    if start <= row["trade_date"] <= end]
        return {"rows": rows, "source": rows[-1]["source"] if rows else None, "failures": failures}
    rows = attempt("baostock_history", lambda: _baostock_history(bs, code, start, end))
    if not rows:
        rows = attempt("ths_history", lambda: _ths_history(code, start, end))
    if not rows:
        rows = [row for row in attempt("mootdx_history", lambda: _mootdx_history(code, limit))
                if start <= row["trade_date"] <= end]
    if not rows:
        rows = attempt("eastmoney_history", lambda: _eastmoney_history(code, start, end, limit=limit))
    return {"rows": rows, "source": rows[-1]["source"] if rows else None, "failures": failures}


def _extend_left_streak(bs, code: str, rows: list[dict]) -> list[dict]:
    """Continue in bounded chunks until a break, an IPO boundary, or provider exhaustion."""
    merged = {row["trade_date"]: row for row in rows}
    for _ in range(6):  # more than seven years of extraordinary unbroken sessions
        ordered = sorted(merged.values(), key=lambda row: row["trade_date"])
        if not _reaches_left_boundary(ordered):
            break
        earliest = datetime.strptime(ordered[0]["trade_date"], "%Y-%m-%d")
        older_end = (earliest - timedelta(days=1)).strftime("%Y-%m-%d")
        older_start = (earliest - timedelta(days=500)).strftime("%Y-%m-%d")
        older = _dated_fallback_history(bs, code, older_start, older_end, 400)
        if not older:
            break
        before = len(merged)
        merged.update({row["trade_date"]: row for row in older})
        if len(merged) == before:
            break
    return sorted(merged.values(), key=lambda row: row["trade_date"])


def _tencent_incremental(codes: Iterable[str], trade_date: str) -> tuple[list[dict], list[str]]:
    """Batch-fetch a settled session. Missing symbols are returned for dated fallbacks."""
    from . import trade_calendar
    from vr import astock

    usable, _reason = trade_calendar.live_quotes_are_close_of(trade_date)
    if not usable:
        return [], list(codes)
    output, missing = [], []
    unique = list(dict.fromkeys(codes))
    for start in range(0, len(unique), 50):
        batch = unique[start:start + 50]
        try:
            quotes = astock.tencent_quote(batch)
        except Exception:
            missing.extend(batch)
            continue
        for code in batch:
            quote = quotes.get(code)
            timestamp = str((quote or {}).get("quote_time") or "")
            quote_day = f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}" if len(timestamp) >= 8 else ""
            if not quote or quote_day != trade_date or quote.get("is_stale"):
                missing.append(code)
                continue
            close, open_ = _number(quote.get("price")), _number(quote.get("open"))
            suspended = not close or not open_
            output.append({
                "code": code, "trade_date": trade_date,
                "open": None if suspended else open_, "high": None if suspended else _number(quote.get("high")),
                "low": None if suspended else _number(quote.get("low")),
                "close": None if suspended else close, "pct_chg": _number(quote.get("change_pct")),
                "volume": None, "amount": (_number(quote.get("amount_wan"), 0.0) * 10_000),
                "turnover_pct": _number(quote.get("turnover_pct")),
                "trade_status": 0 if suspended else 1,
                "is_st": int("ST" in str(quote.get("name") or "").upper()),
                "source": "tencent_close", "fetched_at": _now_text(),
            })
    return output, missing


def _tencent_live_rows(codes: Iterable[str], trade_date: str) -> tuple[list[dict], list[str]]:
    """Fetch today's observable OHLC in batches without treating it as a settled bar.

    These rows are deliberately returned to the caller only.  They must never
    be written to ``daily_bars`` because the open session can still change.
    """
    from vr import astock

    unique = list(dict.fromkeys(codes))
    batches = [unique[start:start + 50] for start in range(0, len(unique), 50)]

    def fetch(batch: list[str]) -> tuple[list[dict], list[str]]:
        quotes = None
        for _attempt in range(2):
            try:
                quotes = astock.tencent_quote(batch)
                break
            except Exception:
                quotes = None
        if quotes is None:
            return [], batch

        rows, missing = [], []
        for code in batch:
            quote = quotes.get(code)
            timestamp = str((quote or {}).get("quote_time") or "")
            quote_day = (
                f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}"
                if len(timestamp) >= 8 and timestamp[:8].isdigit() else ""
            )
            if not quote or quote_day != trade_date or quote.get("is_stale"):
                missing.append(code)
                continue
            close, open_ = _number(quote.get("price")), _number(quote.get("open"))
            suspended = not close or not open_
            rows.append({
                "code": code, "name": str(quote.get("name") or ""),
                "trade_date": trade_date,
                "open": None if suspended else open_,
                "high": None if suspended else _number(quote.get("high")),
                "low": None if suspended else _number(quote.get("low")),
                "close": None if suspended else close,
                "pct_chg": _number(quote.get("change_pct")),
                "volume": None,
                "amount": _number(quote.get("amount_wan"), 0.0) * 10_000,
                "turnover_pct": _number(quote.get("turnover_pct")),
                "trade_status": 0 if suspended else 1,
                "is_st": int("ST" in str(quote.get("name") or "").upper()),
                "source": "tencent_live", "fetched_at": _now_text(),
            })
        return rows, missing

    output, missing = [], []
    # Tencent supports 50 symbols per request.  A bounded pool keeps a full
    # market refresh well below the UI polling interval without opening one
    # connection per symbol.
    with ThreadPoolExecutor(max_workers=min(12, max(1, len(batches)))) as executor:
        futures = [executor.submit(fetch, batch) for batch in batches]
        for future in as_completed(futures):
            rows, absent = future.result()
            output.extend(rows)
            missing.extend(absent)
    return output, sorted(set(missing))


def _drop_not_yet_listed_instruments() -> None:
    """Remove symbols from sessions before their first proven trading bar."""
    with _transaction() as db:
        db.execute("DROP TABLE IF EXISTS temp._corpus_first_trade")
        db.execute(
            "CREATE TEMP TABLE _corpus_first_trade AS "
            "SELECT code,MIN(trade_date) AS first_trade FROM daily_bars "
            "WHERE trade_status=1 GROUP BY code"
        )
        db.execute("CREATE UNIQUE INDEX temp.idx_corpus_first_trade_code ON _corpus_first_trade(code)")
        db.execute(
            "DELETE FROM session_instruments WHERE trade_date<("
            "SELECT first_trade FROM _corpus_first_trade f WHERE f.code=session_instruments.code)"
        )
        db.execute("DROP TABLE temp._corpus_first_trade")


def _drop_after_proven_delisting(codes: Iterable[str], trade_dates: Iterable[str]) -> None:
    """Trim only symbols that the current directory explicitly marks inactive.

    The last real K-line is evidence for a lifecycle boundary only when the
    contemporary security directory also says the symbol is inactive.  Active
    or unknown symbols keep blocking publication when their tail bars are
    absent, rather than being silently treated as delisted.
    """
    inactive = sorted(set(codes))
    dates = sorted(set(trade_dates))
    if not inactive or not dates:
        return
    with _transaction() as db:
        for start in range(0, len(inactive), 500):
            batch = inactive[start:start + 500]
            marks = ",".join("?" for _ in batch)
            db.execute(
                "DELETE FROM session_instruments WHERE code IN (" + marks + ") "
                "AND trade_date>? AND trade_date<=? AND trade_date>("
                "SELECT COALESCE(MAX(b.trade_date),'9999-12-31') FROM daily_bars b "
                "WHERE b.code=session_instruments.code AND b.trade_status=1)",
                (*batch, dates[0], dates[-1]),
            )


def _fill_proven_suspension_gaps(trade_dates: Iterable[str]) -> None:
    """A missing K line between two real sessions proves a listed stock was suspended."""
    dates = sorted(set(trade_dates))
    if not dates:
        return
    with _connect() as db:
        instruments = [dict(row) for row in db.execute(
            "SELECT si.trade_date,si.code,si.is_st FROM session_instruments si LEFT JOIN daily_bars b "
            "ON b.code=si.code AND b.trade_date=si.trade_date "
            f"WHERE si.trade_date IN ({','.join('?' for _ in dates)}) AND b.code IS NULL",
            dates,
        )]
        codes = sorted({row["code"] for row in instruments})
        boundaries = {}
        for start in range(0, len(codes), 800):
            batch = codes[start:start + 800]
            marks = ",".join("?" for _ in batch)
            for row in db.execute(
                f"SELECT code,MIN(trade_date),MAX(trade_date) FROM daily_bars "
                f"WHERE code IN ({marks}) AND trade_status=1 GROUP BY code", batch,
            ):
                boundaries[row[0]] = (row[1], row[2])
    inferred = []
    for item in instruments:
        code, day = item["code"], item["trade_date"]
        first_last = boundaries.get(code)
        if not first_last or not (first_last[0] < day < first_last[1]):
            continue
        inferred.append({
            "code": code, "trade_date": day, "open": None, "high": None, "low": None,
            "close": None, "pct_chg": None, "volume": 0, "amount": 0,
            "trade_status": 0, "is_st": int(item.get("is_st") or 0),
            "source": "inferred_suspension_between_trades", "fetched_at": _now_text(),
        })
    _store_bars(inferred)


def _store_instruments(trade_date: str, instruments: Iterable[dict]) -> None:
    rows = [(
        trade_date, item["code"], item.get("name") or item["code"], item.get("market") or _market(item["code"]),
        item.get("industry") or "未知", item.get("industry_source") or "",
        int(item.get("trade_status", 1)), int(item.get("is_st", 0)),
    ) for item in instruments]
    with _transaction() as db:
        db.execute("DELETE FROM session_instruments WHERE trade_date=?", (trade_date,))
        db.executemany(
            "INSERT INTO session_instruments(trade_date,code,name,market,industry,industry_source,trade_status,is_st) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(trade_date,code) DO UPDATE SET "
            "name=excluded.name,market=excluded.market,industry=excluded.industry,"
            "industry_source=excluded.industry_source,trade_status=excluded.trade_status,is_st=excluded.is_st",
            rows,
        )


def _store_bars(rows: Iterable[dict]) -> None:
    values = [(
        row["code"], row["trade_date"], row.get("open"), row.get("high"), row.get("low"),
        row.get("close"), row.get("pct_chg"), row.get("volume"), row.get("amount"),
        row.get("turnover_pct"),
        int(row.get("trade_status", 1)), int(row.get("is_st", 0)), row["source"],
        row.get("fetched_at") or _now_text(),
    ) for row in rows]
    if not values:
        return
    with _transaction() as db:
        db.executemany(
            "INSERT INTO daily_bars(code,trade_date,open,high,low,close,pct_chg,volume,amount,turnover_pct,"
            "trade_status,is_st,source,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(code,trade_date) DO UPDATE SET open=excluded.open,high=excluded.high,"
            "low=excluded.low,close=excluded.close,pct_chg=excluded.pct_chg,volume=excluded.volume,"
            "amount=excluded.amount,turnover_pct=COALESCE(excluded.turnover_pct,daily_bars.turnover_pct),"
            "trade_status=excluded.trade_status,is_st=excluded.is_st,"
            "source=excluded.source,fetched_at=excluded.fetched_at",
            values,
        )


def _normalised_name(value: str) -> str:
    return re.sub(r"[\s*ＳＴST]+", "", str(value or "").upper())


def _refresh_instrument_master(directory: dict[str, dict]) -> int:
    """Persist canonical identities and conservative Beijing aliases.

    Aliases are merged only when a legacy code and a 920 code share both the
    final three digits and normalised name.  A numeric coincidence alone is
    not sufficient evidence and remains visible as a lifecycle problem.
    """
    now = _now_text()
    aliases = 0
    with _transaction() as db:
        local_names = {row[0]: row[1] for row in db.execute(
            "SELECT code,MAX(name) FROM session_instruments GROUP BY code"
        )}
        first_last = {row[0]: (row[1], row[2]) for row in db.execute(
            "SELECT code,MIN(trade_date),MAX(trade_date) FROM daily_bars GROUP BY code"
        )}
        canonical_bj = {
            code[-3:]: item for code, item in directory.items()
            if code.startswith("920")
        }
        for code, item in directory.items():
            if code.startswith(("43", "83", "87")):
                continue
            first, last = first_last.get(code, (None, None))
            status = "active" if item.get("directory_active") == 1 else "inactive"
            db.execute(
                "INSERT INTO instrument_master(canonical_code,source_code,exchange,security_type,name,"
                "listing_date,delisting_date,current_status,aliases_json,source,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(canonical_code) DO UPDATE SET "
                "source_code=excluded.source_code,exchange=excluded.exchange,security_type=excluded.security_type,"
                "name=excluded.name,listing_date=COALESCE(instrument_master.listing_date,excluded.listing_date),"
                "delisting_date=CASE WHEN excluded.current_status='inactive' THEN COALESCE(excluded.delisting_date,"
                "instrument_master.delisting_date) ELSE NULL END,current_status=excluded.current_status,"
                "source=excluded.source,updated_at=excluded.updated_at",
                (code, item.get("source_code") or code, item.get("market") or _market(code), "stock",
                 item.get("name") or code, first, last if status == "inactive" else None, status,
                 "[]", item.get("industry_source") or "eastmoney_directory", now),
            )
        for legacy, name in local_names.items():
            if not str(legacy).startswith(("43", "83", "87")):
                continue
            candidate = canonical_bj.get(str(legacy)[-3:])
            if not candidate or _normalised_name(name) != _normalised_name(candidate.get("name")):
                continue
            canonical = candidate["code"]
            db.execute(
                "INSERT INTO instrument_aliases(alias_code,canonical_code,source,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(alias_code) DO UPDATE SET canonical_code=excluded.canonical_code,"
                "source=excluded.source,updated_at=excluded.updated_at",
                (legacy, canonical, "bse_code_migration", now),
            )
            alias_rows = [row[0] for row in db.execute(
                "SELECT alias_code FROM instrument_aliases WHERE canonical_code=? ORDER BY alias_code", (canonical,)
            )]
            db.execute(
                "UPDATE instrument_master SET aliases_json=?,updated_at=? WHERE canonical_code=?",
                (json.dumps(alias_rows, ensure_ascii=False), now, canonical),
            )
            aliases += 1
    return aliases


def _load_fresh_master_directory() -> dict[str, dict]:
    """Reuse today's validated master so retries do not refetch 80 directory pages."""
    today = china_now().date().isoformat()
    with _connect() as db:
        updated = db.execute("SELECT MAX(updated_at) FROM instrument_master").fetchone()[0]
        if not updated or str(updated)[:10] != today:
            return {}
        return {
            row["canonical_code"]: {
                "code": row["canonical_code"], "source_code": row["source_code"],
                "name": row["name"], "market": row["exchange"], "security_type": row["security_type"],
                "industry": "未知", "industry_source": row["source"], "trade_status": 1,
                "is_st": int("ST" in str(row["name"]).upper()),
                "directory_active": 1 if row["current_status"] == "active" else 0,
            }
            for row in db.execute(
                "SELECT * FROM instrument_master WHERE security_type='stock' ORDER BY canonical_code"
            )
        }


def _mark_invalid_legacy_bj_quotes(start_date: str, end_date: str) -> int:
    """Quarantine persisted zombie quotes without deleting historical data."""
    with _transaction() as db:
        cursor = db.execute(
            "UPDATE daily_bars SET source='invalid_legacy_bj_quote:' || source,fetched_at=? "
            "WHERE trade_date>=? AND trade_date<=? AND substr(code,1,2) IN ('43','83','87') "
            "AND source LIKE 'tencent%'",
            (_now_text(), start_date, end_date),
        )
        return max(0, int(cursor.rowcount or 0))


def _purge_known_directory_pollution() -> int:
    """Remove invalid memberships globally while retaining every raw bar."""
    predicate = (
        "name LIKE '%指数%' OR name LIKE '%债%' OR upper(name) LIKE '%B股%' "
        "OR (market='SH' AND substr(code,1,2) NOT IN ('60','68')) "
        "OR (market='SZ' AND substr(code,1,2) NOT IN ('00','30')) "
        "OR (market='BJ' AND substr(code,1,3)<>'920')"
    )
    with _transaction() as db:
        before = int(db.execute(f"SELECT COUNT(*) FROM session_instruments WHERE {predicate}").fetchone()[0])
        db.execute(f"DELETE FROM session_instruments WHERE {predicate}")
        db.execute(
            "DELETE FROM instrument_master WHERE security_type<>'stock' OR name LIKE '%指数%' "
            "OR name LIKE '%债%' OR upper(name) LIKE '%B股%' "
            "OR (exchange='SH' AND substr(canonical_code,1,2) NOT IN ('60','68')) "
            "OR (exchange='SZ' AND substr(canonical_code,1,2) NOT IN ('00','30')) "
            "OR (exchange='BJ' AND substr(canonical_code,1,3)<>'920')"
        )
    return before


def _persist_fetch_failures(code: str, start: str, end: str, failures: list[dict]) -> None:
    if not failures:
        return
    with _transaction() as db:
        for failure in failures:
            db.execute(
                "INSERT INTO corpus_fetch_failures(canonical_code,start_date,end_date,provider,category,message,"
                "retriable,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(canonical_code,start_date,end_date,provider) "
                "DO UPDATE SET category=excluded.category,message=excluded.message,retriable=excluded.retriable,"
                "updated_at=excluded.updated_at",
                (code, start, end, failure["provider"], failure["category"], failure["message"],
                 int(failure["retriable"]), _now_text()),
            )


def _materialize_corpus_coverage(
    dates: list[str], source_hits: dict, repair_stats: dict[str, dict],
) -> dict[str, dict]:
    coverage = {}
    now = _now_text()
    with _transaction() as db:
        for day in dates:
            universe = int(db.execute(
                "SELECT COUNT(*) FROM session_instruments WHERE trade_date=?", (day,),
            ).fetchone()[0])
            covered = int(db.execute(
                "SELECT COUNT(*) FROM session_instruments si JOIN daily_bars b "
                "ON b.trade_date=si.trade_date AND b.code=si.code WHERE si.trade_date=?", (day,),
            ).fetchone()[0])
            suspended = int(db.execute(
                "SELECT COUNT(*) FROM session_instruments si JOIN daily_bars b "
                "ON b.trade_date=si.trade_date AND b.code=si.code "
                "WHERE si.trade_date=? AND b.trade_status=0", (day,),
            ).fetchone()[0])
            unknown = max(0, universe - covered)
            unknown_codes = [row[0] for row in db.execute(
                "SELECT si.code FROM session_instruments si LEFT JOIN daily_bars b "
                "ON b.trade_date=si.trade_date AND b.code=si.code "
                "WHERE si.trade_date=? AND b.code IS NULL ORDER BY si.code LIMIT 100", (day,),
            )]
            stats = repair_stats.get(day) or {}
            item = {
                "universe_count": universe, "covered_count": covered,
                "suspended_count": suspended, "lifecycle_excluded_count": int(stats.get("lifecycle_excluded", 0)),
                "unknown_count": unknown, "unknown_codes": unknown_codes,
                "invalid_directory_count": int(stats.get("invalid_removed", 0)),
                "alias_merged_count": int(stats.get("alias_merged", 0)),
            }
            coverage[day] = item
            db.execute(
                "INSERT INTO corpus_date_coverage(trade_date,universe_count,covered_count,suspended_count,"
                "lifecycle_excluded_count,unknown_count,invalid_directory_count,alias_merged_count,"
                "source_hits_json,algorithm_version,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(trade_date) DO UPDATE SET universe_count=excluded.universe_count,"
                "covered_count=excluded.covered_count,suspended_count=excluded.suspended_count,"
                "lifecycle_excluded_count=excluded.lifecycle_excluded_count,unknown_count=excluded.unknown_count,"
                "invalid_directory_count=excluded.invalid_directory_count,alias_merged_count=excluded.alias_merged_count,"
                "source_hits_json=excluded.source_hits_json,algorithm_version=excluded.algorithm_version,"
                "updated_at=excluded.updated_at",
                (day, universe, covered, suspended, item["lifecycle_excluded_count"], unknown,
                 item["invalid_directory_count"], item["alias_merged_count"],
                 json.dumps(source_hits, ensure_ascii=False), ALGORITHM_VERSION, now),
            )
    return coverage


def _load_instruments(trade_date: str) -> list[dict]:
    with _connect() as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM session_instruments WHERE trade_date=? ORDER BY code", (trade_date,),
        )]


def _load_histories(codes: Iterable[str], end_date: str) -> dict[str, list[dict]]:
    unique = list(dict.fromkeys(codes))
    if not unique:
        return {}
    result = {code: [] for code in unique}
    with _connect() as db:
        for start in range(0, len(unique), 800):
            batch = unique[start:start + 800]
            marks = ",".join("?" for _ in batch)
            for row in db.execute(
                f"SELECT * FROM daily_bars WHERE code IN ({marks}) AND trade_date<=? ORDER BY code,trade_date",
                (*batch, end_date),
            ):
                result[row["code"]].append(dict(row))
    return result


def _load_cached_history(code: str, start_date: str, end_date: str) -> list[dict]:
    with _connect() as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM daily_bars WHERE code=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date",
            (code, start_date, end_date),
        )]


def ensure_corpus_window(dates: Iterable[str], progress_callback=None) -> dict:
    """Ensure a dated, full-market OHLC corpus without publishing ladder snapshots.

    Existing instruments and bars are reused.  Every symbol is fetched at most
    once for the requested window, and the return value explicitly separates
    ready dates from dates that still contain unknown coverage gaps.
    """
    ordered = sorted(dict.fromkeys(str(day) for day in dates))
    if not ordered:
        raise ValueError("语料窗口不能为空")

    def report(phase: str, completed: int, total: int, **extra) -> None:
        if progress_callback:
            progress_callback({"phase": phase, "completed": completed, "total": total, **extra})

    globally_removed = _purge_known_directory_pollution()
    directory = _load_fresh_master_directory()
    if directory:
        source_hits = Counter({"instrument_master_cache": 1})
        alias_count = 0
    else:
        directory = {item["code"]: item for item in _full_market_universe(include_inactive=True)}
        alias_count = _refresh_instrument_master(directory)
        source_hits = Counter({"eastmoney_directory": 1})
    invalid_quote_count = _mark_invalid_legacy_bj_quotes(ordered[0], ordered[-1])

    bs = _open_baostock()
    repair_stats: dict[str, dict] = {}
    fetch_failures: list[dict] = []
    try:
        codes = set()
        for index, day in enumerate(ordered, 1):
            stored = _load_instruments(day)
            instruments, source = _session_universe(bs, day, directory)
            before_codes = {item["code"] for item in stored}
            after_codes = set(instruments)
            repair_stats[day] = {
                "before_count": len(before_codes), "after_count": len(after_codes),
                "invalid_removed": len(before_codes - after_codes) + (globally_removed if index == 1 else 0),
                "alias_merged": alias_count,
                "lifecycle_excluded": sum(
                    item.get("market") == "BJ" and code not in after_codes
                    for code, item in directory.items()
                ),
                "invalid_quote_count": invalid_quote_count,
            }
            _store_instruments(day, instruments.values())
            source_hits[source] += 1
            codes.update(instruments)
            report("directory_repair", index, len(ordered), date=day, **repair_stats[day])
            report("corpus_universe", index, len(ordered), date=day)

        with _connect() as db:
            memberships: dict[str, set[str]] = {}
            for row in db.execute(
                "SELECT trade_date,code FROM session_instruments "
                f"WHERE trade_date IN ({','.join('?' for _ in ordered)})",
                ordered,
            ):
                memberships.setdefault(row["code"], set()).add(row["trade_date"])

        # A newly settled session is normally absent for the whole market at
        # once.  Fill it through Tencent's batched close quotes before falling
        # back to one-symbol history providers; otherwise an ordinary daily
        # increment degenerates into thousands of serial history requests.
        latest_day = ordered[-1]
        if latest_day == china_now().date().isoformat():
            with _connect() as db:
                latest_missing = [row[0] for row in db.execute(
                    "SELECT si.code FROM session_instruments si LEFT JOIN daily_bars b "
                    "ON b.code=si.code AND b.trade_date=si.trade_date "
                    "WHERE si.trade_date=? AND b.code IS NULL ORDER BY si.code",
                    (latest_day,),
                )]
            if latest_missing:
                quote_rows, _quote_missing = _tencent_incremental(latest_missing, latest_day)
                if quote_rows:
                    _store_bars(quote_rows)
                    source_hits["tencent_close"] += len(quote_rows)
                report("corpus_bars", len(quote_rows), len(latest_missing), source="tencent_close")

        with _connect() as db:
            missing_codes = {row[0] for row in db.execute(
                "SELECT DISTINCT si.code FROM session_instruments si LEFT JOIN daily_bars b "
                "ON b.code=si.code AND b.trade_date=si.trade_date "
                f"WHERE si.trade_date IN ({','.join('?' for _ in ordered)}) AND b.code IS NULL",
                ordered,
            )}
        work = sorted(missing_codes)
        for index, code in enumerate(work, 1):
            expected = memberships.get(code, set())
            cached = _load_cached_history(code, ordered[0], ordered[-1])
            present = {row["trade_date"] for row in cached}
            missing_for_code = sorted(expected - present)
            if missing_for_code:
                # Fetch one bounded range per symbol.  Existing 181-day tails,
                # for example, cause only the older missing prefix to be read.
                _FETCH_CONTEXT.outcome = None
                fetched = _dated_fallback_history(
                    bs, code, missing_for_code[0], missing_for_code[-1], len(missing_for_code) + 30,
                )
                outcome = getattr(_FETCH_CONTEXT, "outcome", None) or {"rows": fetched, "failures": []}
                _persist_fetch_failures(
                    code, missing_for_code[0], missing_for_code[-1], outcome.get("failures") or [],
                )
                fetch_failures.extend({"code": code, **item} for item in outcome.get("failures") or [])
                if fetched:
                    _store_bars(fetched)
                    source_hits[fetched[-1]["source"]] += 1
            report("corpus_bars", index, len(work), code=code)

        _drop_not_yet_listed_instruments()
        _drop_after_proven_delisting(
            [code for code, item in directory.items() if item.get("directory_active") == 0], ordered,
        )
        with _connect() as db:
            still_missing = db.execute(
                "SELECT 1 FROM session_instruments si LEFT JOIN daily_bars b "
                "ON b.code=si.code AND b.trade_date=si.trade_date "
                f"WHERE si.trade_date IN ({','.join('?' for _ in ordered)}) AND b.code IS NULL LIMIT 1",
                ordered,
            ).fetchone()
        if still_missing:
            _fill_proven_suspension_gaps(ordered)
    finally:
        if bs is not None:
            try:
                bs.logout()
            except Exception:
                pass

    coverage = _materialize_corpus_coverage(ordered, dict(source_hits), repair_stats)
    ready_dates = [day for day in ordered if coverage[day]["universe_count"] and not coverage[day]["unknown_count"]]
    missing_dates = [day for day in ordered if day not in ready_dates]
    return {
        "required_dates": ordered, "ready_dates": ready_dates, "missing_dates": missing_dates,
        "coverage": coverage, "source_hits": dict(source_hits), "repair": repair_stats,
        "fetch_failures": fetch_failures,
    }


def streak_at(rows: list[dict], trade_date: str) -> dict | None:
    """Calculate the two streaks from daily returns, avoiding ex-right false breaks."""
    valid = [row for row in rows if str(row.get("trade_date") or row.get("datetime") or "")[:10] <= trade_date]
    valid.sort(key=lambda row: str(row.get("trade_date") or row.get("datetime") or "")[:10])
    index = next((i for i in range(len(valid) - 1, -1, -1)
                  if str(valid[i].get("trade_date") or valid[i].get("datetime") or "")[:10] == trade_date), None)
    if index is None:
        return None
    current = valid[index]
    if not int(current.get("trade_status", 1)):
        return {"suspended": True, "level": 0, "positive_level": 0, "rising_level": 0,
                "pct_chg": 0.0, "stage_high": False}

    positive = 0
    for position in range(index, -1, -1):
        row = valid[position]
        if not int(row.get("trade_status", 1)):
            break
        open_, close, pct = _number(row.get("open")), _number(row.get("close")), _number(row.get("pct_chg"))
        if open_ is None or close is None:
            break
        is_positive = close > open_
        is_non_falling_doji = math.isclose(close, open_, abs_tol=1e-8) and pct is not None and pct >= 0
        if not (is_positive or is_non_falling_doji):
            break
        positive += 1

    rising = 0
    for position in range(index, -1, -1):
        row = valid[position]
        if not int(row.get("trade_status", 1)) or (_number(row.get("pct_chg")) or 0) <= 0:
            break
        rising += 1

    comparable = 1.0
    comparable_rows = []
    for row in valid[:index + 1]:
        if int(row.get("trade_status", 1)) and _number(row.get("pct_chg")) is not None:
            comparable *= 1 + _number(row.get("pct_chg"), 0.0) / 100
            comparable_rows.append(comparable)
    window = comparable_rows[-120:]
    stage_high = bool(window) and comparable >= max(window) - 1e-12
    return {
        "suspended": False, "positive_level": positive, "rising_level": rising,
        "level": max(positive, rising), "pct_chg": round(_number(current.get("pct_chg"), 0.0), 2),
        "stage_high": stage_high,
    }


def _source_counts(histories: dict[str, list[dict]], trade_date: str) -> dict[str, int]:
    counts = Counter()
    for rows in histories.values():
        row = next((item for item in reversed(rows) if item.get("trade_date") == trade_date), None)
        if row:
            counts[str(row.get("source") or "unknown")] += 1
    return dict(sorted(counts.items()))


def _compute_payload(
    trade_date: str,
    previous_date: str | None,
    instruments: list[dict],
    prior_instruments: list[dict],
    histories: dict[str, list[dict]],
    *,
    live: bool = False,
    source: str = "local_full_market_daily",
    note: str = "",
    available_dates: list[str] | None = None,
) -> dict:
    """Compute a ladder from supplied rows, shared by close and live views."""
    missing, suspended = [], 0
    active = []
    current_stats = {}
    for item in instruments:
        code = item["code"]
        stats = streak_at(histories.get(code, []), trade_date)
        if stats is None:
            missing.append(code)
            continue
        current_stats[code] = stats
        if stats.get("suspended"):
            suspended += 1
            continue
        bar = next((row for row in reversed(histories.get(code, [])) if row.get("trade_date") == trade_date), {})
        is_st = int(bar.get("is_st", item.get("is_st", 0)))
        if is_st or stats["level"] < 3:
            continue
        active.append({
            "code": code, "name": item["name"], "industry": item.get("industry") or "未知",
            "pct_chg": stats["pct_chg"], "level": stats["level"], "eliminated": False,
            "stage_high": bool(stats["stage_high"]),
        })

    if missing:
        raise CoverageError(trade_date, len(instruments), missing, suspended, _source_counts(histories, trade_date))

    broken = []
    active_codes = {item["code"] for item in active}
    if previous_date:
        for item in prior_instruments:
            code = item["code"]
            prior_stats = streak_at(histories.get(code, []), previous_date)
            now_stats = current_stats.get(code)
            if not prior_stats or prior_stats.get("suspended") or prior_stats["level"] < 5:
                continue
            if not now_stats or now_stats.get("suspended") or code in active_codes:
                continue
            broken.append({
                "code": code, "name": item["name"], "industry": item.get("industry") or "未知",
                "pct_chg": now_stats["pct_chg"], "level": prior_stats["level"],
                "eliminated": True, "stage_high": False,
            })

    by_level: dict[int, list[dict]] = {}
    for item in active + broken:
        by_level.setdefault(int(item["level"]), []).append(item)
    levels = []
    for level in sorted(by_level, reverse=True):
        levels.append({
            "level": level,
            "items": sorted(by_level[level], key=lambda row: (row["eliminated"], -row["pct_chg"], row["name"])),
            "overflow": 0,
        })
    sources = _source_counts(histories, trade_date)
    quality = {
        "universe_count": len(instruments), "covered_count": len(instruments), "missing_count": 0,
        "suspended_count": suspended, "source_hits": sources, "algorithm_version": ALGORITHM_VERSION,
    }
    return {
        "available": True, "date": trade_date, "live": live, "updated_at": _now_text(),
        "rule": RULE_TEXT, "source": source, "coverage": "full",
        "note": note, "available_dates": available_dates or [], "levels": levels, "quality": quality,
        "sync": {"state": "ready", "phase": "live" if live else "complete", "completed": len(instruments),
                 "total": len(instruments), "percent": 100.0},
    }


def compute_snapshot(trade_date: str, previous_date: str | None) -> dict:
    instruments = _load_instruments(trade_date)
    if not instruments:
        raise RuntimeError(f"{trade_date} 缺少全市场股票目录")
    prior_instruments = _load_instruments(previous_date) if previous_date else []
    codes = sorted({item["code"] for item in instruments + prior_instruments})
    histories = _load_histories(codes, trade_date)
    return _compute_payload(
        trade_date, previous_date, instruments, prior_instruments, histories,
    )


class CoverageError(RuntimeError):
    def __init__(self, trade_date: str, universe: int, missing: list[str], suspended: int, sources: dict):
        self.trade_date = trade_date
        self.universe = universe
        self.missing = missing
        self.suspended = suspended
        self.sources = sources
        super().__init__(f"{trade_date} 仍缺 {len(missing)}/{universe} 只股票日线")


def _save_snapshots(payloads: Iterable[dict]) -> None:
    with _transaction() as db:
        db.executemany(
            "INSERT INTO ladder_snapshots(trade_date,algorithm_version,payload_json,universe_count,"
            "covered_count,generated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(trade_date) DO UPDATE SET "
            "algorithm_version=excluded.algorithm_version,payload_json=excluded.payload_json,"
            "universe_count=excluded.universe_count,covered_count=excluded.covered_count,"
            "generated_at=excluded.generated_at",
            [
                (payload["date"], ALGORITHM_VERSION, json.dumps(payload, ensure_ascii=False),
                 payload["quality"]["universe_count"], payload["quality"]["covered_count"],
                 payload["updated_at"])
                for payload in payloads
            ],
        )


def _save_snapshot(payload: dict) -> None:
    _save_snapshots([payload])


def _ready_dates() -> list[str]:
    try:
        with _connect() as db:
            return [row[0] for row in db.execute(
                "SELECT trade_date FROM ladder_snapshots WHERE algorithm_version=? ORDER BY trade_date DESC LIMIT ?",
                (ALGORITHM_VERSION, VISIBLE_SESSIONS),
            )][::-1]
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"连阳天梯数据库损坏：{exc}") from exc


def _new_run(requested_date: str | None) -> str:
    run_id = uuid.uuid4().hex
    with _transaction() as db:
        db.execute(
            "INSERT INTO sync_runs(run_id,requested_date,state,phase,started_at) VALUES(?,?,?,?,?)",
            (run_id, requested_date, "building", "queued", _now_text()),
        )
    return run_id


def _update_run(run_id: str, *, state=None, phase=None, completed=None, total=None,
                missing=None, source_hits=None, error=None, finished=False) -> None:
    fields, values = [], []
    for key, value in (("state", state), ("phase", phase), ("completed", completed), ("total", total),
                       ("error", error)):
        if value is not None:
            fields.append(f"{key}=?")
            values.append(value)
    if missing is not None:
        fields.append("missing_json=?")
        values.append(json.dumps(missing, ensure_ascii=False))
    if source_hits is not None:
        fields.append("source_hits_json=?")
        values.append(json.dumps(source_hits, ensure_ascii=False))
    if finished:
        fields.append("finished_at=?")
        values.append(_now_text())
    if not fields:
        return
    with _transaction() as db:
        db.execute(f"UPDATE sync_runs SET {','.join(fields)} WHERE run_id=?", (*values, run_id))


def status() -> dict:
    try:
        with _connect() as db:
            row = db.execute("SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT 1").fetchone()
        ready = _ready_dates()
    except sqlite3.DatabaseError as exc:
        return {"state": "failed", "phase": "database", "completed": 0, "total": 0,
                "percent": 0.0, "available_dates": [], "error": f"连阳天梯数据库损坏：{exc}"}
    if row is None:
        return {"state": "idle", "phase": "idle", "completed": 0, "total": 0,
                "percent": 0.0, "available_dates": ready, "last_ready_date": ready[-1] if ready else None}
    result = dict(row)
    total = int(result.get("total") or 0)
    completed = int(result.get("completed") or 0)
    result.update({
        "percent": round(completed / total * 100, 1) if total else 0.0,
        "missing": json.loads(result.pop("missing_json") or "[]"),
        "source_hits": json.loads(result.pop("source_hits_json") or "{}"),
        "available_dates": ready, "last_ready_date": ready[-1] if ready else None,
    })
    return result


def _strict_response(trade_date: str, sync: dict) -> dict:
    state = "building" if sync.get("state") in {"building", "queued"} else "unavailable"
    note = "正在构建全市场日线，完整前不展示近似榜单。" if state == "building" else (
        sync.get("error") or "该日期尚无完整全市场日线，请重试同步。"
    )
    missing_count = len(sync.get("missing") or [])
    completed = int(sync.get("completed") or 0)
    return {
        "available": False, "date": trade_date, "live": False, "updated_at": _now_text(),
        "rule": RULE_TEXT, "source": "local_full_market_daily", "coverage": state,
        "note": note, "available_dates": sync.get("available_dates") or [], "levels": [],
        "quality": {
            "universe_count": int(sync.get("total") or 0),
            "covered_count": max(0, completed - missing_count),
            "missing_count": missing_count, "suspended_count": 0,
            "source_hits": sync.get("source_hits") or {}, "algorithm_version": ALGORITHM_VERSION,
        },
        "sync": sync,
    }


def _is_live_session(now: datetime, quote_day: str | None, phase_key: str) -> bool:
    """Whether today's quote is mature enough to form an intraday candle."""
    if not quote_day or quote_day != now.strftime("%Y-%m-%d"):
        return False
    if phase_key in {"open", "break", "closing"}:
        return True
    # Keep the final Tencent quote visible after 15:00 while the durable close
    # snapshot is being verified.  Before 09:30 we keep yesterday's close.
    return phase_key == "closed" and now.hour * 60 + now.minute >= 15 * 60


def _build_live_ladder(trade_date: str, previous_date: str) -> dict:
    prior_instruments = _load_instruments(previous_date)
    if not prior_instruments:
        raise RuntimeError(f"{previous_date} 缺少上一完整交易日股票目录")
    codes = [item["code"] for item in prior_instruments]
    live_rows, quote_missing = _tencent_live_rows(codes, trade_date)
    live_by_code = {row["code"]: row for row in live_rows}
    instruments = []
    for previous in prior_instruments:
        row = live_by_code.get(previous["code"])
        current = dict(previous)
        current["trade_date"] = trade_date
        if row:
            current["name"] = row.get("name") or current["name"]
            current["trade_status"] = row["trade_status"]
            current["is_st"] = row["is_st"]
        instruments.append(current)

    histories = _load_histories(codes, previous_date)
    for row in live_rows:
        histories.setdefault(row["code"], []).append(row)
    if quote_missing:
        raise CoverageError(
            trade_date, len(instruments), quote_missing,
            sum(1 for row in live_rows if not row["trade_status"]),
            _source_counts(histories, trade_date),
        )

    dates = sorted(set([*_ready_dates(), trade_date]))[-VISIBLE_SESSIONS:]
    return _compute_payload(
        trade_date, previous_date, instruments, prior_instruments, histories,
        live=True,
        source="tencent_live_plus_local_history",
        note=(
            "盘中天梯按腾讯实时 OHLC 与涨跌幅临时计算，每 15 秒更新；"
            "实时行仅驻留内存，收盘完整性验证通过后自动切换为本地定稿快照。"
        ),
        available_dates=dates,
    )


def _live_ladder(trade_date: str, previous_date: str) -> dict:
    now = time.monotonic()
    cache_key = f"{trade_date}|{previous_date}"
    cached_key = _live_cache.get("key")
    cached_until = _live_cache.get("until")
    if cached_key == cache_key and isinstance(cached_until, float) and now < cached_until:
        return _live_cache["payload"]  # type: ignore[return-value]

    with _LIVE_CACHE_LOCK:
        now = time.monotonic()
        cached_key = _live_cache.get("key")
        cached_until = _live_cache.get("until")
        if cached_key == cache_key and isinstance(cached_until, float) and now < cached_until:
            return _live_cache["payload"]  # type: ignore[return-value]
        try:
            payload = _build_live_ladder(trade_date, previous_date)
        except CoverageError as exc:
            sync = {
                "state": "failed", "phase": "live_quotes",
                "completed": exc.universe - len(exc.missing), "total": exc.universe,
                "percent": round((exc.universe - len(exc.missing)) / exc.universe * 100, 1),
                "missing": exc.missing, "source_hits": exc.sources,
                "available_dates": _ready_dates(),
                "error": f"盘中实时行情仍缺 {len(exc.missing)}/{exc.universe} 只，未展示局部榜单。",
            }
            payload = _strict_response(trade_date, sync)
            payload["live"] = True
            payload["source"] = "tencent_live_plus_local_history"
            payload["note"] = sync["error"]
        _live_cache.clear()
        _live_cache.update({
            "key": cache_key, "until": time.monotonic() + _LIVE_CACHE_SECONDS,
            "payload": payload,
        })
        return payload


def get_current_ladder(trade_date: str | None = None) -> dict:
    """Resolve a requested date, preferring today's in-memory live ladder."""
    from . import trade_calendar

    requested = validate_trade_date(trade_date) if trade_date else None
    now = china_now()
    quote_day = trade_calendar.quote_trade_day()
    phase = trade_calendar.session_phase(now, quote_day)
    live_day = quote_day if _is_live_session(now, quote_day, phase.get("phase_key", "")) else None

    if requested and requested != live_day:
        return get_ladder(requested)
    if live_day and (requested is None or requested == live_day):
        ready = _ready_dates()
        if live_day in ready:
            return get_ladder(live_day)
        previous = next((day for day in reversed(ready) if day < live_day), None)
        if not previous:
            previous = trade_calendar.prev_trade_date(live_day)
        if not previous:
            raise RuntimeError("暂时无法确认盘中行情的上一交易日")
        return _live_ladder(live_day, previous)

    day = requested or trade_calendar.latest_session()
    if not day:
        raise RuntimeError("暂时无法确认最近交易日")
    return get_ladder(day)


def get_ladder(trade_date: str) -> dict:
    day = validate_trade_date(trade_date)
    try:
        with _connect() as db:
            row = db.execute(
                "SELECT payload_json FROM ladder_snapshots WHERE trade_date=? AND algorithm_version=?",
                (day, ALGORITHM_VERSION),
            ).fetchone()
    except sqlite3.DatabaseError as exc:
        sync = {"state": "failed", "phase": "database", "error": f"连阳天梯数据库损坏：{exc}",
                "available_dates": []}
        return _strict_response(day, sync)
    if row:
        payload = json.loads(row[0])
        payload["available_dates"] = _ready_dates()
        payload["sync"] = {**payload.get("sync", {}), **status(), "state": "ready", "phase": "complete"}
        return payload
    current = status()
    if current.get("state") not in {"building", "queued"}:
        try:
            request_sync(day)
        except ValueError as exc:
            current = {**current, "state": "failed", "phase": "window", "error": str(exc)}
            return _strict_response(day, current)
        current = status()
    return _strict_response(day, current)


def request_sync(trade_date: str | None = None) -> dict:
    day = validate_trade_date(trade_date) if trade_date else None
    # The HTTP process may have outlived a crashed daemon thread.  Starting is
    # idempotent and gives manual retry a self-healing path.
    start_worker()
    current = status()
    if current.get("state") in {"building", "queued"}:
        # A DB row can remain queued after a worker/provider failure while the
        # in-memory queue is empty.  Re-enqueueing is safe because the queue is
        # single-slot and _RUN_LOCK keeps execution single-flight.
        try:
            _SYNC_QUEUE.put_nowait(day or current.get("requested_date"))
        except queue.Full:
            pass
        return current
    if current.get("phase") == "database":
        return current
    if day:
        from . import trade_calendar

        latest = trade_calendar.latest_session()
        recent = trade_calendar.trade_dates_ending_at(latest, VISIBLE_SESSIONS) if latest else []
        if day not in recent:
            raise ValueError(f"首期仅支持最近 {VISIBLE_SESSIONS} 个已收盘交易日")
    _new_run(day)
    try:
        _SYNC_QUEUE.put_nowait(day)
    except queue.Full:
        pass
    return status()


def _session_dates(requested_date: str | None) -> list[str]:
    from . import trade_calendar

    end = requested_date or trade_calendar.latest_session()
    if not end:
        raise RuntimeError("暂时无法确认最近已收盘交易日")
    dates = trade_calendar.trade_dates_ending_at(end, WARMUP_SESSIONS + VISIBLE_SESSIONS)
    if end not in dates:
        raise RuntimeError(f"{end} 不是可确认的已收盘交易日")
    if len(dates) < 2:
        raise RuntimeError("交易日历不足，无法构建连阳天梯")
    return dates


@contextmanager
def _process_sync_lock():
    lock = FileLock(_db_path().with_suffix(".sync.lock"))
    if not lock.acquire(blocking=False):
        raise RuntimeError("另一个服务进程正在同步连阳天梯")
    try:
        yield
    finally:
        lock.release()


def _sync_once(requested_date: str | None) -> None:
    with _RUN_LOCK, _process_sync_lock():
        run = status()
        run_id = (str(run.get("run_id")) if run.get("state") in {"building", "queued"} and run.get("run_id")
                  else _new_run(requested_date))
        try:
            dates = _session_dates(requested_date)
            targets = dates[-VISIBLE_SESSIONS:]
            start, end = dates[0], dates[-1]
            previous_by_date = {dates[index]: dates[index - 1] for index in range(1, len(dates))}
            prior_ready = set(_ready_dates())
            if requested_date is None and set(targets).issubset(prior_ready):
                _update_run(run_id, state="ready", phase="complete", completed=len(targets),
                            total=len(targets), missing=[], error="", finished=True)
                return
            incremental = previous_by_date.get(end) in prior_ready and end not in prior_ready
            build_dates = [end] if incremental else targets
            universe_dates = list(build_dates)
            if not incremental:
                before_first = previous_by_date.get(build_dates[0])
                if before_first:
                    universe_dates.insert(0, before_first)
            _update_run(run_id, phase="universe", completed=0, total=0, error="")
            stored_current = {item["code"]: item for item in _load_instruments(end)}
            current_universe = _filter_universe_by_recent_activity(stored_current, targets[0])
            if len(current_universe) < 4000:
                current_universe = {item["code"]: item for item in _full_market_universe()}
                current_universe = _filter_universe_by_recent_activity(
                    current_universe, targets[0],
                )

            bs = None
            try:
                import baostock as baostock_module
                login = baostock_module.login()
                if login.error_code == "0":
                    bs = baostock_module
            except Exception:
                bs = None
            try:
                all_codes = set()
                for day in universe_dates:
                    merged, directory_source = _session_universe(bs, day, current_universe)
                    all_codes.update(merged)
                    _store_instruments(day, merged.values())

                codes = sorted(all_codes)
                _update_run(run_id, phase="history", completed=0, total=len(codes))
                source_hits = Counter()
                failed = []
                if incremental:
                    quote_rows, quote_missing = _tencent_incremental(codes, end)
                    _store_bars(quote_rows)
                    source_hits["tencent_close"] += len(quote_rows)
                    missing_set = set(quote_missing)
                    for index, code in enumerate(codes, start=1):
                        if code not in missing_set:
                            if index == len(codes) or index % 50 == 0:
                                _update_run(run_id, completed=index, total=len(codes), missing=failed,
                                            source_hits=dict(source_hits))
                            continue
                        try:
                            rows = _dated_fallback_history(bs, code, end, end, 10)
                            if rows:
                                _store_bars(rows)
                                source_hits[rows[-1]["source"]] += 1
                            else:
                                failed.append(code)
                        except Exception:
                            failed.append(code)
                        if index == len(codes) or index % 20 == 0:
                            _update_run(run_id, completed=index, total=len(codes), missing=failed,
                                        source_hits=dict(source_hits))
                else:
                    minimum_cached_rows = min(120, len(dates))
                    # Load the corpus once. Opening/migrating SQLite for every
                    # symbol made a warm rebuild look hung even with complete
                    # local data available.
                    cached_histories = _load_histories(codes, end)
                    for index, code in enumerate(codes, start=1):
                        try:
                            cached = [
                                row for row in cached_histories.get(code, [])
                                if row["trade_date"] >= start
                            ]
                            expected_since_first = (
                                len([day for day in dates if day >= cached[0]["trade_date"]])
                                if cached else 0
                            )
                            looks_complete_since_listing = bool(
                                cached and expected_since_first
                                and len(cached) / expected_since_first >= 0.8
                            )
                            cache_is_current = bool(
                                cached and cached[-1]["trade_date"] == end
                                and (
                                    len(cached) >= minimum_cached_rows
                                    or looks_complete_since_listing
                                )
                            )
                            if cache_is_current:
                                rows = cached
                                source_hits["local_cache"] += 1
                            else:
                                fetched = _dated_fallback_history(bs, code, start, end, len(dates) + 30)
                                rows = sorted(
                                    {row["trade_date"]: row for row in [*cached, *fetched]}.values(),
                                    key=lambda row: row["trade_date"],
                                )
                            # Only extend when the streak actually touches our
                            # requested left boundary; a later first bar is an
                            # IPO boundary, not truncated history.
                            if rows and rows[0]["trade_date"] <= start and _reaches_left_boundary(rows):
                                rows = _extend_left_streak(bs, code, rows)
                            if rows:
                                if not cache_is_current:
                                    _store_bars(rows)
                                    source_hits[rows[-1]["source"]] += 1
                            else:
                                failed.append(code)
                        except Exception:
                            failed.append(code)
                        if index == len(codes) or index % 20 == 0:
                            _update_run(run_id, completed=index, total=len(codes), missing=failed,
                                        source_hits=dict(source_hits))
                    # Historical APIs omit suspended BJ sessions.  A current
                    # zero quote closes the right boundary; interior gaps can
                    # then be classified without treating unknowns as covered.
                    bj_codes = [code for code in codes if _market(code) == "BJ"]
                    latest_bj_rows, _ = _tencent_incremental(bj_codes, end)
                    _store_bars(latest_bj_rows)
                _drop_not_yet_listed_instruments()
                _fill_proven_suspension_gaps(universe_dates)
            finally:
                if bs is not None:
                    try:
                        bs.logout()
                    except Exception:
                        pass

            _update_run(run_id, phase="verify", completed=0, total=len(build_dates), missing=[])
            payloads = []
            last_coverage_error = None
            for index, day in enumerate(build_dates, start=1):
                try:
                    payload = compute_snapshot(day, previous_by_date.get(day))
                    payloads.append(payload)
                    last_coverage_error = None
                except CoverageError as exc:
                    last_coverage_error = exc
                _update_run(run_id, completed=index, total=len(build_dates),
                            missing=(last_coverage_error.missing if last_coverage_error else []),
                            source_hits=(last_coverage_error.sources if last_coverage_error else dict(source_hits)))
            if len(payloads) != len(build_dates):
                if last_coverage_error:
                    raise last_coverage_error
                raise RuntimeError(f"仅验证 {len(payloads)}/{len(build_dates)} 个完整交易日")
            _save_snapshots(payloads)
            _update_run(run_id, state="ready", phase="complete", completed=len(build_dates), total=len(build_dates),
                        missing=[], source_hits=dict(source_hits), error="", finished=True)
        except Exception as exc:
            missing = exc.missing if isinstance(exc, CoverageError) else []
            sources = exc.sources if isinstance(exc, CoverageError) else {}
            progress = ({"completed": exc.universe - len(exc.missing), "total": exc.universe}
                        if isinstance(exc, CoverageError) else {})
            _update_run(run_id, state="failed", phase="failed", missing=missing, source_hits=sources,
                        **progress,
                        error=f"{type(exc).__name__}: {exc}"[:1000], finished=True)
            raise


def _worker_loop() -> None:
    global _retry_at, _retry_count, _retry_requested
    while not _WORKER_STOP.is_set():
        requested = None
        try:
            requested = _SYNC_QUEUE.get(timeout=60)
        except queue.Empty:
            try:
                from . import trade_calendar
                latest = trade_calendar.latest_session()
                ready = _ready_dates()
                now = china_now()
                if _retry_at:
                    if time.monotonic() < _retry_at:
                        continue
                    requested = _retry_requested or latest
                else:
                    due = latest and latest not in ready and (now.hour, now.minute) >= (15, 10)
                    if not due:
                        continue
                    requested = latest
                if status().get("state") not in {"building", "queued"}:
                    _new_run(requested)
            except Exception as exc:
                print(f"⚠️ 连阳天梯调度检查失败，稍后重试：{type(exc).__name__}: {exc}")
                if _WORKER_STOP.wait(5):
                    continue
                continue
        if _WORKER_STOP.is_set():
            break
        try:
            _sync_once(requested)
            _retry_count = 0
            _retry_at = 0.0
            _retry_requested = None
        except Exception as exc:
            delay = _RETRY_DELAYS[min(_retry_count, len(_RETRY_DELAYS) - 1)]
            _retry_count += 1
            _retry_at = time.monotonic() + delay
            _retry_requested = requested
            print(f"⚠️ 连阳天梯同步失败，{delay // 60} 分钟后重试：{type(exc).__name__}: {exc}")


def start_worker() -> None:
    global _WORKER_THREAD
    with _WORKER_LOCK:
        if _WORKER_THREAD and _WORKER_THREAD.is_alive():
            return
        _WORKER_STOP.clear()
        _WORKER_THREAD = threading.Thread(target=_worker_loop, name="growth-ladder-sync", daemon=True)
        _WORKER_THREAD.start()


def stop_worker() -> None:
    _WORKER_STOP.set()
    try:
        _SYNC_QUEUE.put_nowait(None)
    except queue.Full:
        pass


def reset_for_tests() -> None:
    """Clear process-only state; tests own their temporary database file."""
    global _retry_at, _retry_count, _retry_requested
    _retry_at = 0.0
    _retry_count = 0
    _retry_requested = None
    _live_cache.clear()
    while True:
        try:
            _SYNC_QUEUE.get_nowait()
        except queue.Empty:
            break
