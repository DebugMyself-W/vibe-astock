"""市场总览数据层 —— 市场情绪 + 板块资金流（板块/大盘级公开数据，不涉个股推荐）。

省流量：全站共享一份缓存（TTL 默认 5 分钟），多个用户/多次打开只抓一次；
盘中 5 分钟刷新足够，非交易时段数据本就不变。数据源全免费、无 key。
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timezone, timedelta

try:  # 兼容 `python vr/app.py` 与测试中的 `import vr.market`
    from . import astock, gstock
except ImportError:  # pragma: no cover - 脚本启动路径
    import astock
    import gstock
from duanxian import trade_calendar
from net_utils import call_with_requests_timeout

BEIJING = timezone(timedelta(hours=8))
_CACHE: dict = {}
_TTL = 300  # 5 分钟；全站共享，省数据源压力
_SOURCE_ERRORS: dict[str, str] = {}
_UPSTREAM_TIMEOUT = 8.0


def _source_ok(name: str) -> None:
    _SOURCE_ERRORS.pop(name, None)


def _source_failed(name: str, exc: Exception) -> None:
    _SOURCE_ERRORS[name] = f"{type(exc).__name__}: {exc}"


def _emotion_unavailable(*, date: str, status: str, note: str) -> dict:
    """Keep the public emotion shape render-safe while withholding incomplete statistics."""
    return {
        "available": False, "date": date, "source_date": date,
        "data_status": status, "data_note": note,
        # 前端的导出、选择和预警均会遍历这些集合；等待定稿不是异常，必须给空集合。
        "ladder": [], "lianban_stocks": [],
    }


def _cached(key: str, fn, valid=bool):
    """TTL 缓存。数据源故障的空结果不缓存（valid 判否），下次请求直接重试。"""
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    val = fn()
    if valid(val):
        _CACHE[key] = (now, val)
    return val


def _num(v) -> int:
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return 0


def _hhmm(v) -> str:
    """Pool HHMMSS value to the display/evaluator HH:MM format."""
    value = astock._numf(v)
    if value is None:
        return ""
    raw = str(int(value)).zfill(6)
    return f"{raw[:2]}:{raw[2:4]}" if raw != "000000" else ""


def _settled_archive_pools(date: str) -> dict | None:
    """Read the exact-date settled archive shared with the review pipeline.

    Eastmoney's online board endpoint does not reliably serve past sessions.
    ``market_facts`` permanently stores the three pools once a session settles,
    so this is the authoritative same-date fallback rather than a date scan.
    """
    try:
        from duanxian import market_facts

        archived = market_facts.pools(date)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(archived, dict) or not isinstance(archived.get("zt"), list) or not archived["zt"]:
        return None

    raw_zt = {
        str(row.get("代码") or "").zfill(6): row
        for row in ((archived.get("raw") or {}).get("zt") or [])
        if isinstance(row, dict)
    }

    def topic_row(row: dict) -> dict:
        code = str(row.get("code") or "").zfill(6)
        raw = raw_zt.get(code) or {}
        price = astock._numf(raw.get("最新价"))
        return {
            "c": code, "n": row.get("name") or "",
            "lbc": _num(row.get("boards")) or 1,
            "p": round(price * 1000) if price is not None else None,
            "zdp": astock._numf(row.get("ret")),
            "amount": astock._numf(row.get("amount")),
            "ltsz": astock._numf(raw.get("流通市值")),
            "hs": astock._numf(row.get("turnover")),
            "fbt": row.get("first_seal"), "lbt": row.get("last_seal"),
            "zbc": astock._numf(row.get("broken_times")),
            "fund": astock._numf(row.get("seal_fund")),
            "hybk": row.get("sector") or "",
        }

    return {
        "date": date,
        "zt": [topic_row(row) for row in archived["zt"] if isinstance(row, dict)],
        "zb": archived.get("zb") if isinstance(archived.get("zb"), list) else None,
        "dt": archived.get("dt") if isinstance(archived.get("dt"), list) else None,
        "source": "settled_archive",
    }


def _previous_settled_zt(date: str) -> list[dict] | None:
    """Exact previous-session close pool used as the promotion denominator."""
    try:
        previous = trade_calendar.prev_trade_date(date)
    except Exception:  # noqa: BLE001
        return None
    archive = _settled_archive_pools(previous) if previous else None
    return archive.get("zt") if archive else None


def _sentiment() -> dict:
    """市场情绪：涨跌家数/涨停跌停/活跃度 + 大盘宽度、题材投机（客观数据机械分档）。"""
    try:
        # AkShare upstream omitted a timeout here.  Inject one only into this
        # source module so a dead route cannot occupy a web worker forever.
        df = call_with_requests_timeout(
            "akshare.stock_feature.stock_market_legu",
            "stock_market_activity_legu",
            timeout=_UPSTREAM_TIMEOUT,
        )
        d = {row["item"]: row["value"] for _, row in df.iterrows()}
        _source_ok("市场宽度")
    except Exception as exc:
        _source_failed("市场宽度", exc)
        return {}
    up, down, flat = _num(d.get("上涨")), _num(d.get("下跌")), _num(d.get("平盘"))
    zt, zt_real = _num(d.get("涨停")), _num(d.get("真实涨停"))
    dt, dt_real = _num(d.get("跌停")), _num(d.get("真实跌停"))
    r = up / max(down, 1)
    if up < 600:
        breadth = "冰点"
    elif r < 0.7:
        breadth = "偏弱"
    elif r < 1.2:
        breadth = "中性"
    elif r < 2.5:
        breadth = "偏强"
    else:
        breadth = "普涨"
    speculation = "亢奋" if zt_real >= 100 else "活跃" if zt_real >= 60 else "普通" if zt_real >= 30 else "冰点"
    return {
        "up": up, "down": down, "flat": flat,
        "zt": zt, "zt_real": zt_real, "dt": dt, "dt_real": dt_real,
        "active": str(d.get("活跃度", "")),
        "breadth": breadth, "speculation": speculation,
        "date": str(d.get("统计日期", "")),
    }


def _sectors() -> list[dict]:
    """行业资金流（按净额降序）。不含领涨股等个股字段。"""
    try:
        f = call_with_requests_timeout(
            "akshare.stock_feature.stock_fund_flow",
            "stock_fund_flow_industry",
            "即时",
            timeout=_UPSTREAM_TIMEOUT,
        )
        f = f.sort_values("净额", ascending=False)
        _source_ok("行业资金流")
    except Exception as exc:
        _source_failed("行业资金流", exc)
        return []
    out = []
    for _, row in f.iterrows():
        out.append({
            "name": str(row["行业"]),
            "pct": round(float(row.get("行业-涨跌幅", 0) or 0), 2),
            "net": round(float(row.get("净额", 0) or 0), 2),
            "inflow": round(float(row.get("流入资金", 0) or 0), 2),
            "outflow": round(float(row.get("流出资金", 0) or 0), 2),
            "firms": _num(row.get("公司家数")),
        })
    return out


def get_overview() -> dict:
    """市场情绪 + 板块资金（含缓存）。资金轮动由前端从 sectors 头尾取。"""
    def build():
        sentiment = _sentiment()
        sectors = _sectors()
        warnings = [f"{name}：{reason}" for name, reason in _SOURCE_ERRORS.items()]
        return {
            "available": bool(sentiment or sectors),
            "sentiment": sentiment,
            "sectors": sectors,
            "warnings": warnings,
            "updated": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M"),
        }
    return _cached("overview", build, valid=lambda v: bool(v.get("sentiment") or v.get("sectors")))


def _settled_emotion_date() -> str | None:
    """Date shown in the historical card.

    The product deliberately keeps that card one completed trading session
    behind on trading days.  On weekends/holidays ``latest_session`` already is
    the most recent close.  The important exception is after today's close:
    ``latest_session`` becomes today, but the historical card must still point
    at the previous session because today's final list belongs to the live card.
    """
    today = datetime.now(BEIJING).strftime("%Y-%m-%d")
    latest = trade_calendar.latest_session()
    if not latest:
        return None
    return trade_calendar.prev_trade_date(today) if latest == today else latest


def _emotion(target_date: str | None = None) -> dict:
    """前一/最近交易日收盘短线情绪及其 2 板以上客观榜单。"""
    # 目标日先由交易日历唯一确定，再严格读取该日。绝不能“从今天向前找第一份
    # 有数据的池”：盘中今天的半成品池也有数据，会把历史卡悄悄换成今日实时榜。
    today = datetime.now(BEIJING).date()
    target = target_date or _settled_emotion_date()
    if not target:
        return _emotion_unavailable(
            date=today.strftime("%Y-%m-%d"), status="unavailable",
            note="无法确认最近收盘交易日，暂不展示收盘统计",
        )
    resolved = target.replace("-", "")
    archive = _settled_archive_pools(target)
    observed = ({"available": False, "rows": None, "date": resolved}
                if archive else astock.em_zt_topic_observed("getTopicZTPool", resolved))
    online_valid = bool(observed.get("available") and observed.get("date") == resolved)
    if archive:
        zt = archive["zt"]
        data_source = "settled_archive"
    elif online_valid:
        zt = observed.get("rows") or []
        data_source = "eastmoney_online"
    else:
        return _emotion_unavailable(
            date=target, status="unavailable",
            note=f"{target} 收盘涨停池暂不可用或返回日期不符，未使用其他交易日数据",
        )

    pools: dict[str, list[dict] | None] = {"zt": zt}
    labels = {"zb": "炸板", "dt": "跌停", "yzt": "昨涨停"}
    endpoints = {
        "zb": ("getTopicZBPool", "fbt:asc"),
        "dt": ("getTopicDTPool", "fund:asc"),
        "yzt": ("getYesterdayZTPool", "zs:desc"),
    }
    missing = []
    for key, (endpoint, sort) in endpoints.items():
        # A same-date close archive is authoritative and much faster than four
        # serial online pool calls.  Only request a dimension the archive lacks.
        if key in ("zb", "dt") and archive and archive.get(key) is not None:
            pools[key] = archive[key]
            continue
        if key == "yzt" and archive:
            previous_zt = _previous_settled_zt(target)
            if previous_zt is not None:
                pools[key] = previous_zt
                continue
        observed = astock.em_zt_topic_observed(endpoint, resolved, sort)
        observed_rows = observed.get("rows") or []
        # getYesterdayZTPool is known to return an empty-but-success envelope
        # for historical dates. A completed A-share session cannot genuinely
        # have zero prior limit-ups here, so prefer the exact previous archive.
        if (observed.get("available") and observed.get("date") == resolved
                and (key != "yzt" or observed_rows)):
            pools[key] = observed_rows
        elif key == "yzt":
            previous_zt = _previous_settled_zt(target)
            if previous_zt is not None:
                pools[key] = previous_zt
                data_source = "settled_archive" if not online_valid else "mixed_archive"
            else:
                missing.append(labels[key])
                pools[key] = None
        else:
            missing.append(labels[key])
            pools[key] = None
    zb, dt, yzt = pools["zb"], pools["dt"], pools["yzt"]

    boards = [_num(p.get("lbc")) or 1 for p in zt]      # 每只连板数（缺省按 1 板）
    lianban = [b for b in boards if b >= 2]             # 2 板及以上（连板）
    # 连板梯队：2/3/4/5+ 各多少家（5 代表 5 板及以上），只保留有家数的档
    tiers = Counter(min(b, 5) for b in lianban)
    ladder = [{"boards": b, "count": tiers[b], "plus": b >= 5} for b in sorted(tiers)]

    # 连板股清单（2 板+，客观公开榜单数据；按连板数、成交额降序）。
    # 产品定位调整（2026-07-05）：从「零标的」→「展示客观榜单但不推荐/不预测/不评分」。
    # 涨停原因题材串（问财，与首板页共用缓存；缺 key/失败 → 空串，不影响主数据）。
    try:
        import firstboard  # 函数内导入：firstboard 顶部 import market，避免循环依赖

        reasons, _reason_err = firstboard.get_reasons(resolved)
    except Exception:  # noqa: BLE001
        reasons = {}
    lianban_stocks = sorted(
        ({
            "code": str(p.get("c", "")), "name": p.get("n", ""),
            "boards": _num(p.get("lbc")) or 1,
            "price": round((astock._numf(p.get("p")) or 0) / 1000, 2),
            "pct": round(astock._numf(p.get("zdp")) or 0, 2),
            "amount": astock._numf(p.get("amount")),      # 成交额,元（'-' 占位归一为 None，防排序对 str 取负崩溃）
            "float_cap": astock._numf(p.get("ltsz")),     # 流通市值,元
            "turnover_pct": astock._numf(p.get("hs")),
            "seal_time": _hhmm(p.get("fbt")),
            "last_seal_time": _hhmm(p.get("lbt")),
            "break_count": (int(astock._numf(p.get("zbc")))
                            if astock._numf(p.get("zbc")) is not None else None),
            "seal_fund": astock._numf(p.get("fund")),
            "industry": p.get("hybk", ""),  # 概念/行业
            "reason": reasons.get(str(p.get("c", "")), ""),  # 涨停原因题材串
            "close_status": "收盘封板",
        } for p in zt if (_num(p.get("lbc")) or 1) >= 2),
        key=lambda x: (-x["boards"], -(x["amount"] or 0)),
    )
    try:
        lianban_stocks = firstboard.attach_settled_traffic(lianban_stocks, resolved)
    except Exception:
        pass

    zt_count = len(zt)
    zb_count = len(zb) if zb is not None else None
    yzt_count = len(yzt) if yzt is not None else None
    attempts = zt_count + zb_count if zb_count is not None else None     # 尝试涨停 = 封住 + 炸板
    seal_rate = round(zt_count / attempts, 3) if attempts else None      # 封板率
    break_rate = round(zb_count / attempts, 3) if attempts is not None and attempts else None
    # 晋级率＝今日 2 板+（＝昨涨停今又停）÷ 昨日涨停家数
    promotion_rate = round(len(lianban) / yzt_count, 3) if yzt_count else None

    date_label = f"{resolved[:4]}-{resolved[4:6]}-{resolved[6:]}"
    return {
        "available": True,
        "date": date_label,
        "source_date": date_label,
        "updated": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M"),
        "data_status": "partial" if missing else "current",
        "data_source": data_source,
        "data_note": (f"{date_label} 主榜单已就绪；{'、'.join(missing)}池暂不可用，对应指标显示为 —" if missing else
                      f"{date_label} 收盘定稿" + ("（本地归档）" if "archive" in data_source else "")),
        "zt_count": zt_count,
        "dt_count": len(dt) if dt is not None else None,
        "zb_count": zb_count,
        "max_boards": max(boards) if boards else 0,
        "lianban_count": len(lianban),
        "ladder": ladder,
        "lianban_stocks": lianban_stocks,
        "seal_rate": seal_rate,
        "break_rate": break_rate,
        "promotion_rate": promotion_rate,
        "yzt_count": yzt_count,
    }


def get_short_term_emotion(force: bool = False) -> dict:
    """上一/最近交易日短线情绪；缓存严格按目标交易日隔离。"""
    target = _settled_emotion_date()
    key = f"emotion:{target or 'unavailable'}"
    if force:
        _CACHE.pop(key, None)
    return _cached(key, lambda: _emotion(target))


def get_turnover_top() -> dict:
    """全市场成交额榜 Top20（客观公开榜单，含缓存 5 分钟）。"""
    def build():
        stocks = astock.market_turnover_rank(20)
        return {
            "available": bool(stocks),
            "stocks": stocks,
            "reason": "" if stocks else "东方财富实时与延时成交额榜当前均不可用",
            "updated": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M"),
        }
    return _cached("turnover_top", build, valid=lambda v: bool(v.get("stocks")))


def get_global_indices() -> list[dict]:
    """全球指数快照（美股 / 港股，含缓存 5 分钟）。空结果不缓存。"""
    return _cached("global_indices", gstock.global_indices, valid=bool)
