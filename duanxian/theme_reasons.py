"""Unified, quota-aware gateway for limit-up reasons.

iWencai is the primary source.  The public Tonghuashun limit-up reveal endpoint
is the date-checked fallback.  Every caller shares single-flight, cache, circuit
breaker and request accounting through this module.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
from typing import Any

from net_utils import market_session

from . import app_settings, trade_calendar
from .util import atomic_write_json, china_now, china_today

_CACHE_DIR = os.path.expanduser("~/.duanxian-agents/cache/zt_reasons")
_STATUS_PATH = os.path.expanduser("~/.duanxian-agents/cache/iwencai_status.json")
_SCHEMA = 2
_STATUS_SCHEMA = 1
_STATE_LOCK = threading.RLock()
_FLIGHTS_LOCK = threading.Lock()
_FLIGHTS: dict[str, threading.Lock] = {}
_MEMORY: dict[str, dict] = {}
_STATUS: dict | None = None
_AUTH_KEY_VALUE: str | None = None


def _ensure_repo_env() -> None:
    """Load the repository .env before inspecting the optional iWencai key."""
    try:
        from .fetchers import _load_env

        _load_env()
    except Exception:  # noqa: BLE001
        pass


def _normalize_date(value: str) -> str:
    text = str(value or "").strip().replace("-", "")
    if not re.fullmatch(r"\d{8}", text):
        raise ValueError(f"日期格式应为 YYYYMMDD 或 YYYY-MM-DD，收到 {value!r}")
    try:
        _dt.datetime.strptime(text, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"日期不是有效日历日期：{value!r}") from exc
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def _clean_reason(value: Any, max_tags: int = 4, max_len: int = 40) -> str:
    text = str(value or "").replace("，", "+").replace(",", "+").strip()
    tags = [part.strip() for part in text.split("+") if part.strip()]
    return "+".join(tags[:max_tags])[:max_len]


def _iso(dt: _dt.datetime | None = None) -> str:
    return (dt or china_now()).isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _blank_status() -> dict:
    return {
        "schema": _STATUS_SCHEMA,
        "day": china_today(),
        "requests_today": 0,
        "successes_today": 0,
        "failures_today": 0,
        "last_attempt_at": None,
        "breaker": None,
    }


def _load_status() -> dict:
    global _STATUS
    if _STATUS is not None:
        return _STATUS
    try:
        with open(_STATUS_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError):
        raw = {}
    base = _blank_status()
    if raw.get("schema") == _STATUS_SCHEMA and raw.get("day") == china_today():
        for key in ("requests_today", "successes_today", "failures_today", "last_attempt_at", "breaker"):
            base[key] = raw.get(key, base[key])
    # An invalid-key breaker deliberately lasts only for this process. A restart
    # or a changed environment key is the recovery mechanism.
    if (base.get("breaker") or {}).get("kind") == "auth_invalid":
        base["breaker"] = None
    _STATUS = base
    return _STATUS


def _persist_status() -> None:
    if _STATUS is not None:
        atomic_write_json(_STATUS_PATH, _STATUS)


def _roll_day(status: dict) -> None:
    today = china_today()
    if status.get("day") != today:
        status.clear()
        status.update(_blank_status())


def _next_beijing_day() -> str:
    now = china_now()
    tomorrow = (now + _dt.timedelta(days=1)).date()
    return _iso(_dt.datetime.combine(tomorrow, _dt.time.min, tzinfo=now.tzinfo))


def _classify_error(exc: Exception) -> tuple[str, int, str | None, str]:
    text = str(exc).lower()
    if any(token in text for token in ("次数已用完", "升级权益", "今日次数")):
        return "quota_exhausted", 0, _next_beijing_day(), "今日问财次数已用完，已切换公开源"
    if "401" in text or "invalid_token" in text or "unauthorized" in text:
        return "auth_invalid", 0, None, "问财鉴权失败（HTTP 401，IWENCAI_API_KEY 无效或已过期），本次服务期间已切换公开源"
    if "429" in text:
        return "rate_limited", 15, None, "问财请求受限，暂停 15 分钟"
    return "network_cooldown", 5, None, "问财网络异常，暂停 5 分钟"


def _set_breaker(kind: str, minutes: int, until: str | None, reason: str) -> None:
    now = china_now()
    with _STATE_LOCK:
        status = _load_status()
        _roll_day(status)
        status["breaker"] = {
            "kind": kind,
            "until": until or (_iso(now + _dt.timedelta(minutes=minutes)) if minutes else None),
            "reason": reason,
        }
        _persist_status()


def _active_breaker() -> dict | None:
    global _AUTH_KEY_VALUE
    key = os.environ.get("IWENCAI_API_KEY") or ""
    with _STATE_LOCK:
        status = _load_status()
        _roll_day(status)
        breaker = status.get("breaker")
        if breaker and breaker.get("kind") == "auth_invalid" and _AUTH_KEY_VALUE is not None and key != _AUTH_KEY_VALUE:
            breaker = None
            status["breaker"] = None
        _AUTH_KEY_VALUE = key
        if breaker and breaker.get("until"):
            until = _parse_iso(breaker.get("until"))
            if until is not None and china_now() >= until:
                breaker = None
                status["breaker"] = None
        _persist_status()
        return dict(breaker) if breaker else None


def _record_http(success: bool) -> None:
    with _STATE_LOCK:
        status = _load_status()
        _roll_day(status)
        status["requests_today"] = int(status.get("requests_today") or 0) + 1
        field = "successes_today" if success else "failures_today"
        status[field] = int(status.get(field) or 0) + 1
        status["last_attempt_at"] = _iso()
        _persist_status()


def current_status() -> dict:
    _ensure_repo_env()
    breaker = _active_breaker()
    settings = app_settings.get_settings()
    with _STATE_LOCK:
        status = dict(_load_status())
    return {
        "configured": bool(os.environ.get("IWENCAI_API_KEY")),
        "source_priority": ["iwencai", "ths_public"],
        "intraday_refresh_minutes": settings["iwencai_intraday_refresh_minutes"],
        "day": status.get("day"),
        "requests_today": int(status.get("requests_today") or 0),
        "successes_today": int(status.get("successes_today") or 0),
        "failures_today": int(status.get("failures_today") or 0),
        "last_attempt_at": status.get("last_attempt_at"),
        "circuit_state": breaker.get("kind") if breaker else "ready",
        "blocked_until": breaker.get("until") if breaker else None,
        "circuit_reason": breaker.get("reason") if breaker else None,
    }


def _cache_path(date: str) -> str:
    return os.path.join(_CACHE_DIR, f"{date}.json")


def _read_disk(date: str) -> dict | None:
    path = _cache_path(date)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    reasons = raw.get("reasons")
    if raw.get("date") != date or not isinstance(reasons, dict):
        return None
    if raw.get("schema") == 1:
        try:
            fetched = _dt.datetime.fromtimestamp(os.path.getmtime(path), tz=china_now().tzinfo)
        except OSError:
            fetched = china_now()
        return {
            "schema": _SCHEMA, "date": date, "reasons": reasons,
            "source": "legacy", "fetched_at": _iso(fetched),
            "settled": trade_calendar.is_settled(date), "error": None,
        }
    if raw.get("schema") == _SCHEMA:
        return raw
    return None


def _cached(date: str, settled_now: bool, refresh_minutes: int) -> dict | None:
    env = _MEMORY.get(date) or _read_disk(date)
    if not env:
        return None
    _MEMORY[date] = env
    if env.get("settled"):
        return {**env, "cache_hit": True}
    if settled_now:
        return None  # force the one closing-final fetch
    fetched = _parse_iso(env.get("fetched_at"))
    # A fully failed lookup gets a short negative cache. This protects both
    # upstreams from sequential page loads while still retrying soon.
    ttl = (refresh_minutes if refresh_minutes else 60) if env.get("reasons") else 1
    if fetched is not None and china_now() - fetched < _dt.timedelta(minutes=ttl):
        return {**env, "cache_hit": True}
    return None


def _fetch_iwencai(date: str) -> tuple[dict[str, str], str | None]:
    if not os.environ.get("IWENCAI_API_KEY"):
        return {}, "未配置 IWENCAI_API_KEY"
    try:
        from .fetchers import _iwencai_client_cls
        Client = _iwencai_client_cls()
        client = Client(timeout=12, max_retries=1)
    except Exception as exc:  # noqa: BLE001
        return {}, f"问财客户端初始化失败（{type(exc).__name__}）"
    compact = date.replace("-", "")
    reasons: dict[str, str] = {}
    error: str | None = None
    row_count = 0
    try:
        for page in range(1, 4):
            try:
                frame = client.query(f"{date}涨停的股票 涨停原因", page=page, limit=50)
                _record_http(True)
            except Exception as exc:  # noqa: BLE001
                _record_http(False)
                kind, minutes, until, note = _classify_error(exc)
                _set_breaker(kind, minutes, until, note)
                error = note
                break
            if frame is None or len(frame) == 0:
                break
            row_count += len(frame)
            code_cols = [col for col in frame.columns if "代码" in col]
            reason_cols = [col for col in frame.columns if "涨停原因" in col]
            dated = {match.group(1): col for col in reason_cols if (match := re.search(r"\[(\d{8})\]", col))}
            if not code_cols or compact not in dated:
                error = "问财未返回目标交易日的涨停原因列"
                break
            cc, rc = code_cols[0], dated[compact]
            for _, row in frame.iterrows():
                code = str(row[cc])[:6]
                reason = _clean_reason(row[rc])
                if re.fullmatch(r"\d{6}", code) and reason and reason.lower() not in {"nan", "none", "--"}:
                    reasons.setdefault(code, reason)
            if len(frame) < 50:
                break
            if page == 3:
                error = "问财返回超过 3 页，已按部分结果调用公开源补齐"
        if reasons and row_count > len(reasons) and error is None:
            error = f"问财有 {row_count - len(reasons)} 条缺少可用原因，已调用公开源补齐"
    finally:
        try:
            client._session.close()
        except Exception:  # noqa: BLE001
            pass
    return reasons, error or (None if reasons else "问财未返回涨停原因")


def _fetch_public(date: str) -> tuple[dict[str, str], str | None]:
    compact = date.replace("-", "")
    params = {
        "limit": 200, "date": compact, "filter": "HS,GEM2STAR",
        "field": "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004",
        "order_field": "330324", "order_type": "0",
    }
    reasons: dict[str, str] = {}
    fetched = 0
    try:
        with market_session() as session:
            for page in range(1, 11):
                response = session.get(
                    "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool",
                    params={**params, "page": page}, timeout=12,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.10jqka.com.cn/"},
                )
                response.raise_for_status()
                body = response.json()
                if body.get("status_code") != 0:
                    return reasons, "公开涨停揭秘返回业务错误"
                data = body.get("data") or {}
                if str(data.get("date")) != compact:
                    return {}, "公开涨停揭秘日期不符，未使用其他交易日数据"
                rows = data.get("info")
                if not isinstance(rows, list):
                    return reasons, "公开涨停揭秘返回格式异常"
                for row in rows:
                    code, reason = str(row.get("code", "")), _clean_reason(row.get("reason_type"))
                    if re.fullmatch(r"\d{6}", code) and reason and reason not in {"--", "-", "nan"}:
                        reasons[code] = reason
                fetched += len(rows)
                total = (data.get("page") or {}).get("total")
                if not rows or (total is not None and fetched >= int(total)) or (total is None and len(rows) < params["limit"]):
                    return reasons, None if reasons else "公开涨停揭秘未返回原因"
        return reasons, "公开涨停揭秘超过分页上限，原因数据不完整"
    except Exception as exc:  # noqa: BLE001
        return reasons, f"公开涨停揭秘暂不可用（{type(exc).__name__}）"


def _flight(date: str) -> threading.Lock:
    with _FLIGHTS_LOCK:
        return _FLIGHTS.setdefault(date, threading.Lock())


def get(date_value: str, *, force: bool = False) -> dict:
    """Return reasons plus immutable provenance metadata.

    ``force`` only bypasses a fresh intraday cache. It never bypasses a settled
    result or a breaker, so forced review runs cannot spend quota repeatedly.
    """
    _ensure_repo_env()
    date = _normalize_date(date_value)
    lock = _flight(date)
    with lock:
        settings = app_settings.get_settings()
        refresh = settings["iwencai_intraday_refresh_minutes"]
        settled_now = trade_calendar.is_settled(date)
        cached = _cached(date, settled_now, refresh)
        if cached and (cached.get("settled") or not force):
            return cached

        breaker = _active_breaker()
        primary: dict[str, str] = {}
        primary_error: str | None = None
        allow_iwencai = bool(os.environ.get("IWENCAI_API_KEY")) and not breaker and (settled_now or refresh != 0)
        if allow_iwencai:
            primary, primary_error = _fetch_iwencai(date)
        elif breaker:
            primary_error = breaker.get("reason")
        elif refresh == 0 and not settled_now:
            primary_error = "设置为仅收盘调用问财"
        else:
            primary_error = "未配置 IWENCAI_API_KEY"

        fallback: dict[str, str] = {}
        fallback_error: str | None = None
        if primary_error or not primary:
            fallback, fallback_error = _fetch_public(date)
        reasons = {**fallback, **primary}
        if primary and fallback:
            source = "mixed"
        elif primary:
            source = "iwencai"
        elif fallback:
            source = "ths_public"
        else:
            source = "unavailable"
        errors = [part for part in (primary_error, fallback_error) if part]
        env = {
            "schema": _SCHEMA,
            "date": date,
            "reasons": reasons,
            "source": source,
            "fetched_at": _iso(),
            "settled": bool(settled_now and reasons),
            "error": "；".join(errors) if errors else None,
            "cache_hit": False,
        }
        _MEMORY[date] = env
        if reasons:
            atomic_write_json(_cache_path(date), {key: value for key, value in env.items() if key != "cache_hit"})
        return env


def get_reasons(date_value: str, *, force: bool = False) -> tuple[dict[str, str], str | None]:
    env = get(date_value, force=force)
    return env.get("reasons") or {}, env.get("error")


def reset_memory_for_tests() -> None:
    """Clear process-local state; intentionally not used by production paths."""
    global _STATUS, _AUTH_KEY_VALUE
    with _STATE_LOCK:
        _MEMORY.clear()
        _STATUS = None
        _AUTH_KEY_VALUE = None
    with _FLIGHTS_LOCK:
        _FLIGHTS.clear()
