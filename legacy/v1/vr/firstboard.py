"""首板分析数据层 —— 今日首板涨停股（连板数=1）+ 涨停原因题材串。

短线投资实例专属模块（不回推开源仓库）。
- 首板名单：东财涨停池（astock.em_zt_topic_pool，免费无 key）。
- 涨停原因：按交易日查询问财，失败时由同花顺公开涨停揭秘补充。
  两源均核对返回日期，均失败才留空，错误信息不暴露凭据。
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

try:  # 兼容 `python vr/app.py` 与测试中的 `import vr.firstboard`
    from . import astock
    from .market import BEIJING, _num
except ImportError:  # pragma: no cover - 脚本启动路径
    import astock
    from market import BEIJING, _num
from net_utils import market_session
from duanxian import trade_calendar
from duanxian.util import atomic_write_json, china_now

_CACHE: dict = {}
_TTL = 600  # 10 分钟；涨停原因盘中变化不快
_ERROR_TTL = 60
_LIMIT_GENE_CACHE: dict[str, tuple[float, dict]] = {}
_LIMIT_GENE_TTL = 60 * 60
_ARCHIVE_DIR = Path(os.path.expanduser("~/.duanxian-agents/cache/first_board"))
_ARCHIVE_SCHEMA = 2
_ARCHIVE_KEEP = 7


def _price_limit(code: str, previous_close: float) -> float:
    ratio = Decimal("0.30") if code.startswith(("4", "8", "920")) else Decimal("0.20") if code.startswith(("300", "301", "688")) else Decimal("0.10")
    return float((Decimal(str(previous_close)) * (Decimal("1") + ratio)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def limit_gene(code: str) -> dict:
    """One-year Tencent metrics: a limit-up counts only when the close is sealed."""
    code = str(code).strip()
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("code 必须是 6 位股票代码")
    now = time.monotonic()
    cached = _LIMIT_GENE_CACHE.get(code)
    # A transient data-source error must not keep the dialog unavailable for an hour.
    if cached and now - cached[0] < (_LIMIT_GENE_TTL if cached[1].get("available") else 60):
        return cached[1]
    try:
        rows = []
        bars, source = astock.limit_gene_daily_kline(code, offset=260)
        for bar in bars:
            try:
                close, high = float(bar["close"]), float(bar["high"])
                if close > 0 and high > 0:
                    rows.append((str(bar.get("datetime") or bar.get("date") or "")[:10], close, high))
            except (KeyError, TypeError, ValueError):
                continue
        rows.sort(key=lambda item: item[0])
        if len(rows) < 20:
            raise ValueError(f"历史日K仅返回 {len(rows)} 条，无法覆盖近一年")
        sealed = touched = recent_sealed = 0
        for index in range(1, len(rows)):
            _day, close, high = rows[index]
            limit = _price_limit(code, rows[index - 1][1])
            # Count only confirmed closing seals. A high that merely touched
            # the cap remains a failed seal and only contributes to the rate's denominator.
            if high >= limit:
                touched += 1
            if close >= limit:
                sealed += 1
                if index >= len(rows) - 60:
                    recent_sealed += 1
        success_rate = sealed / touched if touched else None
        score = round(min(sealed / 20, 1) * 40 + (success_rate or 0) * 40 + min(recent_sealed / 6, 1) * 20)
        result = {"available": True, "window_days": len(rows) - 1, "from": rows[0][0], "to": rows[-1][0],
                  "limit_up_count": sealed, "touched_limit_count": touched, "seal_success_rate": success_rate,
                  "recent_60_limit_count": recent_sealed, "stock_character_score": score,
                  "source": source, "note": "涨停次数仅统计收盘封住涨停；盘中触板但收盘未封不计入。"}
    except Exception as exc:  # noqa: BLE001
        result = {"available": False, "reason": f"涨停基因数据暂不可用：{exc}"}
    _LIMIT_GENE_CACHE[code] = (now, result)
    return result


def _archive_path(date: str) -> Path:
    """One final first-board snapshot per trading day; date is YYYYMMDD."""
    return _ARCHIVE_DIR / f"{date}.json"


def _load_archive(date: str) -> dict | None:
    """Read a previously finalised board without treating a broken file as data."""
    path = _archive_path(date)
    try:
        import json
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None
    payload = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(payload, dict) or payload.get("date") != date:
        return None
    # A cached archive is accepted only when its counts still match the exact
    # settled fact pool for the same date.  Known incomplete historical files
    # are rebuilt from that pool and marked reconstructed (never used as a
    # contemporaneous signal in performance statistics).
    try:
        from duanxian import market_facts
        iso_date = f"{date[:4]}-{date[4:6]}-{date[6:]}" if len(date) == 8 else date
        facts_path = Path(getattr(market_facts, "_CACHE_DIR", "")) / f"{iso_date}.json"
        if facts_path.is_file():
            import json as _json
            facts = _json.loads(facts_path.read_text(encoding="utf-8"))
            pool = (facts or {}).get("pools") or {}
            zt = pool.get("zt") or []
            first_count = sum(1 for row in zt if int(row.get("boards") or row.get("lbc") or 1) == 1)
            stocks = payload.get("stocks") if isinstance(payload.get("stocks"), list) else []
            missing_display_fields = payload.get("provenance") == "reconstructed" and any(
                not isinstance(stock, dict)
                or stock.get("price") is None
                or stock.get("amount") is None
                or stock.get("float_cap") is None
                for stock in stocks
            )
            if (len(zt) != int(payload.get("total_zt") or -1)
                    or first_count != int(payload.get("first_count") or -1)
                    or missing_display_fields):
                rebuilt = _reconstruct_from_pool(date, pool)
                if rebuilt:
                    _persist_final(date, rebuilt)
                    payload = {**rebuilt, "provenance": "reconstructed"}
    except Exception:
        pass
    # Brief experimental theme projections may exist in local archives. They are
    # no longer part of this endpoint and must not leak back after rollback.
    payload = {key: value for key, value in payload.items() if key != "theme_summary"}
    payload["stocks"] = [
        {key: value for key, value in stock.items() if key not in {"theme", "themes"}}
        for stock in payload.get("stocks", [])
    ]
    # 首板收盘档案通常在 15:00 先生成，而完整复盘（含环境灯）会更晚落盘。
    # 若当时尚无复盘，档案里的个股灯会缺失；复盘出现后在首次读取时补挂并
    # 回写一次，后续刷新直接复用，避免永远显示「待计算」。
    stocks = payload.get("stocks") if isinstance(payload.get("stocks"), list) else []
    if stocks and any(
        not isinstance(stock.get("traffic_light"), dict)
        or not isinstance(stock.get("theme_group"), dict)
        for stock in stocks
    ):
        try:
            from duanxian import review_store

            iso_date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
            review = review_store.load(iso_date) or {}
            environment = review.get("board_traffic_light") or {}
            if environment.get("mode") == "settled":
                enriched = attach_settled_traffic(stocks, date)
                if (len(enriched) == len(stocks)
                        and all(isinstance(stock.get("traffic_light"), dict) for stock in enriched)):
                    payload = {**payload, "stocks": enriched}
                    saved = _persist_final(date, payload)
                    return saved
        except Exception:
            # 复盘仍在生成或局部缓存暂不可用时保留原档案，下次读取继续尝试。
            pass
    return {
        **payload,
        "finalized": True,
        "persisted_at": raw.get("persisted_at"),
        "retention_days": _ARCHIVE_KEEP,
    }


def _reconstruct_from_pool(date: str, pool: dict) -> dict | None:
    """Recreate a first-board payload from the normalized exact fact pool."""
    rows = pool.get("zt") if isinstance(pool, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    raw_rows = ((pool.get("raw") or {}).get("zt") or []) if isinstance(pool.get("raw"), dict) else []
    raw_by_code = {
        str(row.get("代码") or row.get("code") or row.get("c") or "").zfill(6): row
        for row in raw_rows if isinstance(row, dict)
    }
    reasons, reason_note = get_reasons(date)

    def first_value(*values):
        return next((value for value in values if value is not None and value != ""), None)

    stocks = []
    for row in rows:
        boards = int(row.get("boards") or row.get("lbc") or 1)
        if boards != 1:
            continue
        code = str(row.get("code") or row.get("c") or "").zfill(6)
        raw = raw_by_code.get(code, {})
        price = first_value(row.get("close"), row.get("price"), raw.get("最新价"), row.get("p"))
        pct = first_value(row.get("ret"), row.get("pct"), raw.get("涨跌幅"), row.get("zdp"))
        last_seal = first_value(row.get("last_seal"), raw.get("最后封板时间"), row.get("lbt"))
        stocks.append({
            "code": code,
            "name": row.get("name") or row.get("n") or raw.get("名称") or "",
            "price": round(float(price), 2) if price is not None else None,
            "pct": round(float(pct), 2) if pct is not None else None,
            "amount": first_value(row.get("amount"), raw.get("成交额")),
            "float_cap": first_value(row.get("float_cap"), raw.get("流通市值"), row.get("ltsz")),
            "industry": row.get("sector") or row.get("hybk") or raw.get("所属行业") or "",
            "seal_time": _hhmm(first_value(row.get("first_seal"), raw.get("首次封板时间"), row.get("fbt"))),
            "last_seal_time": _hhmm(last_seal),
            "sealed_minutes": _sealed_minutes(last_seal),
            "seal_fund": first_value(row.get("seal_fund"), raw.get("封板资金"), row.get("fund")),
            "break_count": first_value(row.get("broken_times"), raw.get("炸板次数"), row.get("zbc"), 0),
            "turnover_pct": first_value(row.get("turnover"), raw.get("换手率"), row.get("hs")),
            "reason": reasons.get(code) or row.get("reason") or "",
        })
    covered = sum(bool(stock["reason"]) for stock in stocks)
    notes = ["由当日事实池重建；不进入当时落盘信号回测", reason_note]
    if covered < len(stocks):
        notes.append(f"首板原因覆盖 {covered}/{len(stocks)}，缺项留空")
    payload = {"date": date, "total_zt": len(rows), "first_count": len(stocks),
               "reason_note": "；".join(filter(None, notes)), "stocks": stocks,
               "provenance": "reconstructed"}
    try:
        return {**payload, "stocks": attach_settled_traffic(stocks, date)}
    except Exception:
        return payload


def _prune_archives() -> None:
    """Keep the most recent seven trading-day snapshots; never affect live data."""
    try:
        paths = sorted(
            (path for path in _ARCHIVE_DIR.glob("????????.json") if re.fullmatch(r"\d{8}", path.stem)),
            key=lambda path: path.stem,
            reverse=True,
        )
        for path in paths[_ARCHIVE_KEEP:]:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def _persist_final(date: str, payload: dict) -> dict:
    """Atomically archive a valid close snapshot and return the UI metadata."""
    record = {
        "schema": _ARCHIVE_SCHEMA,
        "date": date,
        "persisted_at": china_now().isoformat(timespec="seconds"),
        "data": payload,
    }
    if atomic_write_json(str(_archive_path(date)), record):
        _prune_archives()
        return {**payload, "finalized": True, "persisted_at": record["persisted_at"],
                "retention_days": _ARCHIVE_KEEP}
    # A read-only or full disk must never make the market-data response fail.
    return {**payload, "finalized": True, "retention_days": _ARCHIVE_KEEP}


def _is_final_session(date: str) -> bool:
    """Only archive a day once its session has actually closed."""
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    try:
        return trade_calendar.is_settled(iso)
    except Exception:
        # Historical dates are immutable; for today fail closed and keep it live.
        return date < china_now().strftime("%Y%m%d")


def _clean_reason(text: str, max_tags: int = 4, max_len: int = 40) -> str:
    """题材串清洗：统一分隔符、限标签数与总长（问财多用 '+' 分隔）。"""
    text = text.replace("，", "+").replace(",", "+").strip()
    tags = [t.strip() for t in text.split("+") if t.strip()]
    out = "+".join(tags[:max_tags])
    return out[:max_len]


def _sealed_minutes(value) -> int | None:
    """Trading minutes from the final seal to 15:00, excluding the lunch break."""
    text = _hhmm(value)
    if not text:
        return None
    hour, minute = (int(part) for part in text.split(":"))
    current = hour * 60 + minute
    morning_close, afternoon_open, close = 11 * 60 + 30, 13 * 60, 15 * 60
    if current <= morning_close:
        return max(0, morning_close - current) + close - afternoon_open
    if current < afternoon_open:
        return close - afternoon_open
    return max(0, close - current)


def _error_note(exc: Exception) -> str:
    # Never send upstream response bodies (which may echo credentials) to the browser.
    message = str(exc).lower()
    if "401" in message or "invalid_token" in message:
        return "问财鉴权失败（HTTP 401，密钥无效或已过期）；请更新服务端 IWENCAI_API_KEY 并重启服务"
    if "403" in message:
        return "问财拒绝访问（HTTP 403），请检查密钥接口权限"
    if "429" in message:
        return "问财请求受限（HTTP 429），请检查额度或稍后重试"
    if "timeout" in message or "timed out" in message:
        return "问财请求超时，请检查网络后重试"
    return f"问财查询失败（{type(exc).__name__}），请检查网络或接口状态"


def _fetch_reasons(date: str) -> tuple[dict, str | None]:
    """Compatibility wrapper; new code must use the unified gateway."""
    from duanxian.theme_reasons import _fetch_iwencai

    return _fetch_iwencai(datetime.strptime(date, "%Y%m%d").strftime("%Y-%m-%d"))


def _fetch_public_reasons(date: str) -> tuple[dict, str | None]:
    """Compatibility wrapper around the gateway's date-checked public source."""
    from duanxian.theme_reasons import _fetch_public

    return _fetch_public(datetime.strptime(date, "%Y%m%d").strftime("%Y-%m-%d"))


_REASONS_CACHE: dict = {}


def get_reasons(date: str) -> tuple[dict, str | None]:
    """当日全部涨停股的涨停原因（全站共用统一缓存与熔断）。"""
    from duanxian.theme_reasons import get_reasons as shared_get_reasons

    return shared_get_reasons(date)


def _hhmm(v) -> str:
    """池内时间字段（HHMMSS 整数）→ 'HH:MM'。"""
    s = str(_num(v)).zfill(6)
    return f"{s[:2]}:{s[2:4]}" if len(s) == 6 else ""


def _first_board() -> dict:
    today = datetime.now(BEIJING).date()
    # 自动模式也必须走带 qdate 回执的严格接口。旧逻辑直接读 pool：周末请求
    # 20260829 时，数据源会把 20260828 的池子原样回给来，却被错误贴成 29 日。
    for back in range(8):
        d = (today - timedelta(days=back)).strftime("%Y%m%d")
        # 盘后档案优先：刷新页面/重启服务都不应重新消耗原因接口，也不应把
        # 已定稿的首板灯又换回盘中计算口径。
        archived = _load_archive(d)
        if archived is not None:
            return archived
        observed = astock.em_zt_topic_observed("getTopicZTPool", d)
        if observed.get("available") and observed.get("date") == d:
            result = _build_board(d, observed.get("rows") or [])
            return _persist_final(d, result) if _is_final_session(d) else result
    return {}


def _build_board(resolved: str, zt: list[dict]) -> dict:
    reasons, reason_note = get_reasons(resolved)

    stocks = [
        {
            "code": str(p.get("c", "")),
            "name": p.get("n", ""),
            "price": round((astock._numf(p.get("p")) or 0) / 1000, 2),
            "pct": round(astock._numf(p.get("zdp")) or 0, 2),
            "amount": astock._numf(p.get("amount")),
            "float_cap": astock._numf(p.get("ltsz")),
            "industry": p.get("hybk", ""),
            "seal_time": _hhmm(p.get("fbt")),
            "last_seal_time": _hhmm(p.get("lbt")),
            "sealed_minutes": _sealed_minutes(p.get("lbt")),
            "seal_fund": astock._numf(p.get("fund")),
            "break_count": _num(p.get("zbc")),
            "turnover_pct": astock._numf(p.get("hs")),
            "reason": reasons.get(str(p.get("c", "")), ""),
        }
        for p in zt
        if (_num(p.get("lbc")) or 1) == 1
    ]  # 池接口 sort=fbt:asc，天然按首封时间升序（早封在前）

    covered = sum(bool(s["reason"]) for s in stocks)
    if covered < len(stocks):
        reason_note = "；".join(filter(None, [reason_note, f"首板原因覆盖 {covered}/{len(stocks)}，缺项留空"]))
    stocks = attach_settled_traffic(stocks, resolved)
    return {
        "date": resolved,
        "total_zt": len(zt),
        "first_count": len(stocks),
        "reason_note": reason_note,
        "stocks": stocks,
    }


def attach_settled_traffic(stocks: list[dict], resolved: str) -> list[dict]:
    """Attach close-time lights using the same frozen pool as the review snapshot."""
    try:
        from duanxian import market_facts, review_store
        from duanxian.board_traffic_light import (
            evaluate_stock, industry_support_groups, settled_theme_groups,
        )
        from duanxian.theme_tree import primary_direction_groups

        date = f"{resolved[:4]}-{resolved[4:6]}-{resolved[6:]}"
        review = review_store.load(date) or {}
        environment = review.get("board_traffic_light") or {}
        reasons = {str(stock.get("code")): str(stock.get("reason") or "") for stock in stocks}
        by_code, by_theme = settled_theme_groups(market_facts.pools(date), reasons)
        # 事实池暂缺个别行时仍用同一事件树算法处理首板源数据；不再按行业冒充题材。
        source_directions, source_themes = primary_direction_groups(stocks, reasons)
        settled_industries = industry_support_groups(by_code.values())
        source_industries = industry_support_groups(stocks)
        out = []
        for stock in stocks:
            try:
                code = str(stock.get("code") or "")
                canonical = by_code.get(code)
                board_row = {**stock, **canonical} if canonical else stock
                direction = ((canonical or {}).get("theme_group") if canonical
                             else source_directions.get(code)) or source_directions.get(code) or {}
                theme = str(direction.get("name") or "")
                members = (by_theme if canonical else source_themes).get(theme, [])
                industry = str(board_row.get("industry") or "")
                industry_members = (settled_industries if canonical else source_industries).get(industry, [])
                light = evaluate_stock(environment, board_row, board_row=board_row,
                                       theme_count=len(members), theme_members=members, date=date,
                                       industry_count=len(industry_members),
                                       industry_members=industry_members,
                                       direction=direction,
                                       as_of="15:00:00", mode="settled", persistent=True)
                out.append({**stock, "theme_group": direction, "traffic_light": light})
            except Exception:
                # 单只异常只保留该行待计算，不能拖垮整批首板质量灯。
                out.append(stock)
        return out
    except Exception:
        return stocks


def _requested_board(date: str) -> dict:
    archived = _load_archive(date)
    if archived is not None:
        return archived
    observed = astock.em_zt_topic_observed("getTopicZTPool", date)
    valid = observed.get("available") and observed.get("date") == date
    rows = observed.get("rows") if valid else None
    if rows:
        result = _build_board(date, rows)
        return _persist_final(date, result) if _is_final_session(date) else result
    # An unavailable historical pool is not evidence of zero limit-ups.
    return {"date": date, "total_zt": 0 if valid else None,
            "first_count": 0 if valid else None, "stocks": [], "reason_note": None,
            "data_note": "所选日期涨停池为空（可能为休市日或当日尚无涨停）" if valid else
                "所选日期涨停池暂不可用或返回日期不符，未使用其他交易日数据"}


def get_first_board(date: str | None = None) -> dict:
    """Default is the latest board; final sessions are archived for seven trading days."""
    today = datetime.now(BEIJING).date()
    requested = None
    if date:
        if not re.fullmatch(r"(?:\d{8}|\d{4}-\d{2}-\d{2})", date):
            raise ValueError("交易日格式应为 YYYY-MM-DD")
        try:
            day = datetime.strptime(date.replace("-", ""), "%Y%m%d").date()
        except ValueError as exc:
            raise ValueError("交易日不是有效日期") from exc
        if day > today:
            raise ValueError("不能查询未来交易日")
        requested = day.strftime("%Y%m%d")
    key = f"first_board:{requested or 'auto:' + today.isoformat()}"
    now = time.time()
    hit = _CACHE.get(key)
    ttl = (_ERROR_TTL if requested is None else _TTL) if hit and all(
        s.get("reason") for s in hit[1].get("stocks", [])) else _ERROR_TTL
    if hit and hit[1].get("data_note"):
        ttl = _ERROR_TTL
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = _requested_board(requested) if requested else _first_board()
    if val:
        _CACHE[key] = (now, val)
        if len(_CACHE) > 64:
            del _CACHE[min(_CACHE, key=lambda k: _CACHE[k][0])]
    return val
