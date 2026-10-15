"""A股全栈数据层 —— 移植自 a-stock-data 工具包（五层数据源，自包含）。

分级依赖：
  - 行情（腾讯）        : 仅需标准库 urllib —— 永远可用
  - 研报（东财）+ PDF   : 仅需 requests —— 轻量必装
  - 一致预期/新闻/公告  : akshare（惰性导入，缺失时优雅报错）
  - K线/财务/F10        : mootdx（惰性导入，缺失时优雅报错）

合规：本模块只按用户传入的代码返回客观数据，不预置任何标的、不排名、不建议。
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import time
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from net_utils import market_session, market_urlopen

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def get_prefix(code: str) -> str:
    """6 位代码 → 交易所前缀。5 开头是沪市基金/ETF（51/56/58 等），深市基金 15/16 开头走默认 sz。"""
    if code.startswith(("4", "8", "920")):
        return "bj"
    if code.startswith(("6", "9", "5")):
        return "sh"
    return "sz"


class DependencyMissing(RuntimeError):
    """惰性依赖未安装时抛出，前端据此提示 pip install。"""


# ---------------------------------------------------------------------------
# Layer 1 · 行情（腾讯财经，仅标准库，不封 IP）
# ---------------------------------------------------------------------------

def _fetch_gtimg(prefixed_codes: list[str]) -> str:
    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed_codes)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with market_urlopen(req, timeout=10) as resp:
        return resp.read().decode("gbk")


def _parse_gtimg(data: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        code = key[2:]

        def num(i: int) -> float:
            try:
                return float(vals[i]) if vals[i] else 0.0
            except (ValueError, IndexError):
                return 0.0

        legacy_bj = key.startswith("bj") and code.startswith(("43", "83", "87"))
        result[code] = {
            "name": vals[1],
            "quote_time": vals[30] if len(vals) > 30 else None,
            "price": num(3),
            "last_close": num(4),
            "open": num(5),
            "volume_hands": num(6),
            "change_amt": num(31),
            "change_pct": num(32),
            "high": num(33),
            "low": num(34),
            "amount_wan": num(37),
            "turnover_pct": num(38),
            "pe_ttm": num(39),
            "amplitude_pct": num(43),
            "mcap_yi": num(44),
            "float_mcap_yi": num(45),
            "pb": num(46),
            "limit_up": num(47),
            "limit_down": num(48),
            "vol_ratio": num(49),
            "pe_static": num(52),
            # Since the BSE 920xxx migration, old 43/83/87 symbols are query
            # aliases. Tencent may still answer them with frozen zero-volume
            # records, so callers must never persist those as fresh bars.
            "is_stale": legacy_bj,
            "stale_reason": "legacy_bj_code_requires_canonical_920" if legacy_bj else None,
        }
    return result


def tencent_quote(codes: list[str]) -> dict[str, dict]:
    """批量个股实时行情：现价 / 涨跌 / PE / PB / 市值 / 换手 / 涨跌停。"""
    prefixed = [f"{get_prefix(c)}{c}" for c in codes]
    return _parse_gtimg(_fetch_gtimg(prefixed))


def tencent_quote_observed(codes: list[str]) -> dict[str, dict]:
    """严格读取集合竞价公开盘口；缺失值保持缺失，不据此推测。"""
    result = {}
    codes = list(dict.fromkeys(code for code in codes if re.fullmatch(r"\d{6}", code)))
    for start in range(0, len(codes), 50):
        try:
            raw = _fetch_gtimg([get_prefix(code) + code for code in codes[start:start + 50]])
        except Exception:
            continue
        for line in raw.split(";"):
            if '"' not in line or "=" not in line:
                continue
            values = line.split('"')[1].split("~")
            if len(values) <= 32:
                continue
            code = line.split("=")[0].strip()[-6:]
            if code not in codes:
                continue
            if code.startswith(("43", "83", "87")):
                continue
            try:
                timestamp = datetime.strptime(values[30], "%Y%m%d%H%M%S").isoformat() + "+08:00"
                price, pct = float(values[3]), float(values[32])
                if price <= 0 or not math.isfinite(price) or not math.isfinite(pct):
                    continue
            except (ValueError, IndexError):
                continue

            def observed_number(index: int) -> float | None:
                try:
                    value = float(values[index])
                    return value if math.isfinite(value) and value >= 0 else None
                except (ValueError, IndexError):
                    return None

            matched_hands = observed_number(6)
            bid1_hands, ask1_hands = observed_number(10), observed_number(20)
            bid1_volume = bid1_hands * 100 if bid1_hands is not None else None
            ask1_volume = ask1_hands * 100 if ask1_hands is not None else None
            queue_total = (bid1_volume or 0) + (ask1_volume or 0)
            result[code] = {
                "price": price, "pct": pct, "last_close": observed_number(4),
                "quote_time": timestamp, "name": values[1],
                "matched_volume": matched_hands * 100 if matched_hands is not None else None,
                "matched_amount": matched_hands * 100 * price if matched_hands is not None else None,
                "bid1_volume": bid1_volume, "ask1_volume": ask1_volume,
                "dominant_queue_volume": max(bid1_volume or 0, ask1_volume or 0) if queue_total else None,
                "order_imbalance": round(((bid1_volume or 0) - (ask1_volume or 0)) / queue_total, 4) if queue_total else None,
            }
    return result


# A股大盘指数（前缀规则与个股不同，固定带前缀代码）
A_INDICES = ["sh000001", "sz399001", "sz399006", "sh000300"]


def index_quote() -> list[dict]:
    """A股大盘指数实时行情（上证/深证成指/创业板指/沪深300）。"""
    parsed = _parse_gtimg(_fetch_gtimg(A_INDICES))
    out = []
    for full in A_INDICES:
        q = parsed.get(full[2:])
        if q:
            out.append({"name": q["name"], "price": q["price"], "change_pct": q["change_pct"], "change_amt": q["change_amt"]})
    return out


# ---------------------------------------------------------------------------
# Layer 2 · 研报（东财 reportapi，仅 requests）
# ---------------------------------------------------------------------------

_REPORT_API = "https://reportapi.eastmoney.com/report/list"
_PDF_TPL = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"


def _report_session():
    s = market_session()
    s.headers.update({"User-Agent": UA, "Referer": "https://data.eastmoney.com/"})
    return s


def _normalize_end_date(end_date: str | None) -> str | None:
    if not end_date:
        return None
    return datetime.strptime(end_date, "%Y-%m-%d").strftime("%Y-%m-%d")


def _dated_on_or_before(value: object, cutoff: str) -> bool:
    try:
        observed = datetime.strptime(str(value or "")[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return False
    return observed <= cutoff


def eastmoney_reports(code: str, max_pages: int = 3, end_date: str | None = None) -> list[dict]:
    """按个股代码拉研报列表（qType=0），可限制发布日期截止日。"""
    cutoff = _normalize_end_date(end_date)
    session = _report_session()
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        params = {
            "industryCode": "*", "pageSize": "100", "industry": "*",
            "rating": "*", "ratingChange": "*",
            "beginTime": "2000-01-01", "endTime": cutoff or "2030-01-01",
            "pageNo": str(page), "fields": "", "qType": "0",
            "orgCode": "", "code": code, "rcode": "",
            "p": str(page), "pageNum": str(page), "pageNumber": str(page),
        }
        r = session.get(_REPORT_API, params=params, timeout=30)
        d = r.json()
        rows = d.get("data") or []
        if not rows:
            break
        out.extend(rows)
        if page >= (d.get("TotalPage", 1) or 1):
            break
        time.sleep(0.3)
    return [row for row in out if _dated_on_or_before(row.get("publishDate"), cutoff)] if cutoff else out


def eastmoney_industry_reports(keywords: list[str] | None = None, days: int = 90, max_pages: int = 3) -> list[dict]:
    """按行业拉研报（qType=1）——适合产业链 / 主题级检索。keywords 在标题上过滤。"""
    from datetime import date, timedelta

    session = _report_session()
    end = date.today()
    begin = end - timedelta(days=days)
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        params = {
            "industryCode": "*", "pageSize": "100", "industry": "*",
            "rating": "*", "ratingChange": "*",
            "beginTime": begin.isoformat(), "endTime": end.isoformat(),
            "pageNo": str(page), "fields": "", "qType": "1",
            "orgCode": "", "code": "", "rcode": "",
        }
        r = session.get(_REPORT_API, params=params, timeout=30)
        rows = r.json().get("data") or []
        if not rows:
            break
        out.extend(rows)
        time.sleep(0.3)
    if keywords:
        out = [r for r in out if any(k in r.get("title", "") for k in keywords)]
    return out


def pdf_url(info_code: str) -> str:
    return _PDF_TPL.format(info_code=info_code)


# ---------------------------------------------------------------------------
# Layer 3/4/5 · akshare 惰性封装（一致预期 / 新闻 / 公告 / 基本面）
# ---------------------------------------------------------------------------

def _akshare():
    try:
        import akshare as ak
        return ak
    except ImportError as e:
        raise DependencyMissing("akshare 未安装：pip install akshare") from e


def profit_forecast(code: str) -> list[dict]:
    """机构一致预期 EPS（同花顺）。"""
    ak = _akshare()
    df = ak.stock_profit_forecast_ths(symbol=code, indicator="预测年报每股收益")
    return df.to_dict("records") if df is not None and not df.empty else []


def stock_news(code: str, limit: int = 20, end_date: str | None = None) -> list[dict]:
    """个股新闻（东财），可限制发布日期截止日。"""
    cutoff = _normalize_end_date(end_date)
    ak = _akshare()
    df = ak.stock_news_em(symbol=code)
    if df is None or df.empty:
        return []
    rows = df.to_dict("records")
    if cutoff:
        rows = [row for row in rows if _dated_on_or_before(row.get("发布时间"), cutoff)]
    return rows[:limit]


def individual_info(code: str) -> dict:
    """个股基本面（东财）：行业 / 总股本 / 上市时间等。"""
    ak = _akshare()
    df = ak.stock_individual_info_em(symbol=code)
    if df is None or df.empty:
        return {}
    return {str(row["item"]): row["value"] for _, row in df.iterrows()}


def disclosure(code: str) -> list[dict]:
    """巨潮公告全文列表（akshare cninfo，本环境不稳，保留作备用）。"""
    ak = _akshare()
    market = "沪市" if code.startswith("6") else ("北交所" if code.startswith("8") else "深市")
    df = ak.stock_zh_a_disclosure_report_cninfo(symbol=code, market=market)
    return df.head(30).to_dict("records") if df is not None and not df.empty else []


def announcements(code: str, limit: int = 15) -> list[dict]:
    """个股近期公告（东财公开接口，仅 requests，稳定）。返回 日期/标题/类型/详情链接。"""
    with market_session() as session:
        r = session.get(
            "https://np-anotice-stock.eastmoney.com/api/security/ann",
            params={"sr": -1, "page_size": limit, "page_index": 1, "ann_type": "A",
                    "client_source": "web", "stock_list": code, "f_node": 0, "s_node": 0},
            headers={"User-Agent": UA}, timeout=20,
        )
    lst = (r.json().get("data") or {}).get("list") or []
    out = []
    for a in lst:
        cols = [c.get("column_name") for c in (a.get("columns") or []) if c.get("column_name")]
        art = a.get("art_code", "")
        out.append({
            "date": (a.get("notice_date", "") or "")[:10],
            "title": a.get("title", ""),
            "type": cols[0] if cols else "",
            "url": f"https://data.eastmoney.com/notices/detail/{code}/{art}.html" if art else "",
        })
    return out


# ---------------------------------------------------------------------------
# mootdx 惰性封装（K线 / 财务 / F10）
# ---------------------------------------------------------------------------

def _mootdx_client():
    try:
        from duanxian.growth_ladders import tdx_client
        return tdx_client()
    except ImportError as e:
        raise DependencyMissing("mootdx 未安装：pip install mootdx") from e


def _tencent_daily_kline(code: str, offset: int = 60, *, adjusted: bool = True) -> list[dict]:
    """腾讯日 K；通用 K 线回退用前复权，涨停计算用未复权。"""
    symbol = f"{get_prefix(code)}{code}"
    suffix = ",qfq" if adjusted else ""
    query = urllib.parse.urlencode({"param": f"{symbol},day,,,{offset}{suffix}"})
    request = urllib.request.Request(
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?{query}",
        headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"},
    )
    with market_urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    node = (payload.get("data") or {}).get(symbol) or {}
    rows = (node.get("qfqday") if adjusted else (node.get("day") or node.get("qfqday"))) or []
    result = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        try:
            result.append({
                "datetime": row[0], "open": float(row[1]), "close": float(row[2]),
                "high": float(row[3]), "low": float(row[4]), "vol": float(row[5]),
                "amount": float(row[6]) if len(row) > 6 and row[6] not in (None, "") else None,
            })
        except (TypeError, ValueError):
            continue
    return result


def kline(code: str, category: int = 4, offset: int = 60) -> list[dict]:
    """K线：mootdx 优先；日线按独立数据源逐级回退。"""
    try:
        client = _mootdx_client()
        df = client.bars(symbol=code, frequency=category, offset=offset)
        rows = df.to_dict("records") if df is not None and not df.empty else []
        if rows:
            return rows
    except Exception:
        pass
    if category not in (4, 9):
        return []
    for fetcher in (_tencent_daily_kline, _akshare_tencent_daily_kline, _eastmoney_daily_kline):
        try:
            rows = fetcher(code, offset)
            if rows:
                return rows
        except Exception:
            continue
    return []


def raw_daily_kline(code: str, offset: int = 260) -> list[dict]:
    """未复权腾讯日 K，用于涨停价与涨停基因计算。"""
    return _tencent_daily_kline(code, offset, adjusted=False)


def _akshare_tencent_daily_kline(code: str, offset: int = 260) -> list[dict]:
    import akshare as ak

    symbol = f"{get_prefix(code)}{code}"
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=max(420, offset * 2))).strftime("%Y%m%d")
    frame = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start, end_date=end)
    if frame is None or getattr(frame, "empty", True):
        return []
    result = []
    for row in frame.to_dict("records"):
        try:
            result.append({
                "datetime": str(row["date"]), "open": float(row["open"]),
                "close": float(row["close"]), "high": float(row["high"]),
                "low": float(row["low"]), "vol": float(row.get("volume") or 0),
                "amount": float(row["amount"]) if row.get("amount") is not None else None,
            })
        except (KeyError, TypeError, ValueError):
            continue
    return result[-offset:]


def _eastmoney_daily_kline(code: str, offset: int = 260) -> list[dict]:
    code = str(code).strip()
    market = "1" if code.startswith(("5", "6", "9")) else "0"
    response = em_get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "secid": f"{market}.{code}", "klt": "101", "fqt": "0", "lmt": str(offset),
            "end": "20500101", "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        },
        headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}, timeout=12,
    )
    response.raise_for_status()
    rows = ((response.json().get("data") or {}).get("klines") or [])
    result = []
    for row in rows:
        columns = row.split(",") if isinstance(row, str) else []
        if len(columns) < 6:
            continue
        try:
            result.append({
                "datetime": columns[0], "open": float(columns[1]), "close": float(columns[2]),
                "high": float(columns[3]), "low": float(columns[4]), "vol": float(columns[5]),
                "amount": float(columns[6]) if len(columns) > 6 and columns[6] else None,
            })
        except (TypeError, ValueError):
            continue
    return result


def limit_gene_daily_kline(code: str, offset: int = 260) -> tuple[list[dict], str]:
    """涨停基因日 K：腾讯未复权优先，并保留两个可追溯回退源。"""
    try:
        rows = raw_daily_kline(code, offset)
    except Exception:
        rows = []
    if len(rows) >= 20:
        return rows, "腾讯日K（未复权）"
    try:
        tencent_rows = _akshare_tencent_daily_kline(code, offset)
    except Exception:
        tencent_rows = []
    if len(tencent_rows) >= 20:
        return tencent_rows, "腾讯历史日K（AkShare 客户端）"
    try:
        fallback = _eastmoney_daily_kline(code, offset)
    except Exception:
        fallback = []
    if len(fallback) >= 20:
        return fallback, "东财历史日K（腾讯日K不可用时回退）"
    return rows or tencent_rows or fallback, "腾讯日K与东财历史日K"


def finance(code: str) -> dict:
    """季报财务快照（37 字段）。"""
    client = _mootdx_client()
    df = client.finance(symbol=code)
    if df is None or (hasattr(df, "empty") and df.empty):
        return {}
    return df.to_dict("records")[0] if hasattr(df, "to_dict") else dict(df)


# ---------------------------------------------------------------------------
# 估值计算
# ---------------------------------------------------------------------------

def calc_peg(pe: float, cagr: float) -> float:
    if cagr <= 0:
        return float("inf")
    return pe / (cagr * 100)


def pe_digestion(current_pe: float, cagr: float, target_pe: float = 30) -> float:
    if current_pe <= target_pe:
        return 0.0
    if cagr <= 0:
        return float("inf")
    return math.log(current_pe / target_pe) / math.log(1 + cagr)


def financials(code: str) -> dict:
    """财务关键指标（同花顺财务摘要，最新报告期）—— 干净可靠的营收/净利/ROE/毛利率等。

    注：mootdx finance() 的营收/净利数值不可靠(实测放大数倍)，故财务摘要走此源。
    """
    ak = _akshare()
    df = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
    if df is None or df.empty:
        return {}
    row = df.iloc[-1].to_dict()  # 最新报告期（按报告期升序，取末行）

    def g(k):
        v = row.get(k)
        return None if v in (False, "false", "", None) else v

    return {
        "period": g("报告期"),
        "revenue": g("营业总收入"), "revenue_yoy": g("营业总收入同比增长率"),
        "net_profit": g("净利润"), "net_profit_yoy": g("净利润同比增长率"),
        "eps": g("基本每股收益"), "bvps": g("每股净资产"),
        "roe": g("净资产收益率"), "gross_margin": g("销售毛利率"), "net_margin": g("销售净利率"),
        "op_cf_ps": g("每股经营现金流"),
    }


def valuation_percentile(code: str, period: str = "近五年") -> dict:
    """历史估值分位（百度股市通）：PE-TTM / PB 的当前值 + 历史 20/50/80 分位带 + 所处分位。

    只表达"处于历史什么位置"，不划买卖线（理杏仁式中立呈现）。
    """
    ak = _akshare()

    def _q(vals: list, p: float) -> float:
        if not vals:
            return 0.0
        idx = p * (len(vals) - 1)
        lo = int(idx)
        if lo + 1 >= len(vals):
            return vals[-1]
        frac = idx - lo
        return vals[lo] * (1 - frac) + vals[lo + 1] * frac

    import pandas as pd
    metrics, gaps = {}, []
    for key, ind in (("pe_ttm", "市盈率(TTM)"), ("pb", "市净率")):
        try:
            df = ak.stock_zh_valuation_baidu(symbol=code, indicator=ind, period=period)
            df = df.copy()
            daycol, valuecol = df.columns[:2]
            df[daycol] = pd.to_datetime(df[daycol], errors="coerce")
            df[valuecol] = pd.to_numeric(df[valuecol], errors="coerce")
            valid = df[daycol].notna() & df[valuecol].map(math.isfinite)
            if not valid.all(): gaps.append(f"{ind}剔除了无效日期或数值，分位基于剩余样本")
            df = df[valid].sort_values(daycol)
            raw = df.iloc[:, 1].astype(float).tolist()
            if not raw:
                gaps.append(f"{ind}没有取得有效历史样本")
                continue
            cur = float(raw[-1])
            s = sorted(raw)
            below = sum(1 for x in s if x < cur)
            metrics[key] = {
                "current": round(cur, 2), "as_of": str(df.iloc[-1, 0])[:10],
                "percentile": round(below / max(len(s) - 1, 1) * 100, 1),
                "min": round(s[0], 2), "max": round(s[-1], 2),
                "p20": round(_q(s, 0.2), 2), "p50": round(_q(s, 0.5), 2), "p80": round(_q(s, 0.8), 2),
                "n": len(s),
            }
        except Exception:
            gaps.append(f"{ind}历史取数失败，未取得不等于没有历史")
    return {"period": "近5年", "metrics": metrics, "gaps": gaps}


def full_valuation(code: str) -> dict:
    """单票完整估值：腾讯行情 + 一致预期 EPS + 前向PE/PEG/消化年数。"""
    quotes = tencent_quote([code])
    q = quotes.get(code)
    if not q:
        raise ValueError(f"未取到 {code} 的行情")

    price = q["price"]
    out = {
        "name": q["name"], "code": code, "price": price,
        "quote_time": q.get("quote_time"),
        "mcap_yi": q["mcap_yi"], "pe_ttm": q["pe_ttm"], "pb": q["pb"],
        "eps_26e": None, "eps_27e": None, "pe_26e": None,
        "cagr_pct": None, "peg": None, "digest_years": None, "analyst_count": 0,
    }

    try:
        rows = profit_forecast(code)
    except DependencyMissing:
        out["forecast_note"] = "一致预期需安装 akshare"
        return out
    except Exception as exc:
        # 一致预期是可选增强项。它所在的同花顺接口或市场数据代理失败时，
        # 仍应返回已取得的腾讯行情、PE/PB 和市值，而不是让整个个股研究 502。
        out["forecast_note"] = f"一致预期取数失败（{type(exc).__name__}），其余估值仍可用"
        return out

    def _eps(row: dict):
        # 同花顺对覆盖不全的股票会缺「均值」或给 '-' 占位，硬取会让整只票的估值接口 502
        try:
            return float(str(row.get("均值", "")).replace(",", ""))
        except ValueError:
            return None

    eps_26 = eps_27 = None
    for row in rows:
        y = str(row.get("年度", ""))
        if "2026" in y:
            eps_26 = _eps(row)
            try:
                out["analyst_count"] = int(float(row.get("预测机构数") or 0))
            except (TypeError, ValueError):
                pass
        elif "2027" in y:
            eps_27 = _eps(row)

    out["eps_26e"], out["eps_27e"] = eps_26, eps_27
    if eps_26 and eps_26 > 0:
        pe_26e = price / eps_26
        out["pe_26e"] = round(pe_26e, 1)
        if eps_27:
            cagr = eps_27 / eps_26 - 1
            out["cagr_pct"] = round(cagr * 100, 0)
            peg = calc_peg(pe_26e, cagr)
            out["peg"] = round(peg, 2) if peg != float("inf") else None
            dig = pe_digestion(pe_26e, cagr)
            out["digest_years"] = round(dig, 1) if dig != float("inf") else None
    return out


# ===========================================================================
# Layer 3/4/10 · 资金面 / 筹码 / 信号（东财数据中心，移植自 a-stock-data v3.3）
#
# 合规：以下端点全部按【用户传入的单个代码】返回该股的客观公开数据（龙虎榜记录、
# 融资融券、大宗交易、股东户数、分红、资金流、解禁、板块归属、投资者问答），
# 不预置标的、不做主观评分、不给买卖建议。
# 定位调整（2026-07-05）：涨停池 / 全市场成交额榜等【客观公开榜单】现已用于产品 UI
# （每日复盘的连板股 + 成交额 TOP20）——如实展示公开榜单≠荐股，只要不附推荐/评分/预测。
# 仍不做：主观评分排名、买卖点位、涨跌预测；龙虎榜个股名单/强势股/人气榜等带隐性倾向的甩单暂不进 UI。
# ===========================================================================

_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_EM_MIN_INTERVAL = 1.0          # 两次东财请求最小间隔（秒），内置防封节流
_em_last_call = [0.0]
_EM_REQUEST_LOCK = threading.Lock()

def em_get(url: str, params: dict | None = None, headers: dict | None = None, timeout: int = 15):
    """东财请求限流；网络路径由统一市场数据策略决定。"""
    with _EM_REQUEST_LOCK:
        now = time.monotonic()
        start = max(now, _em_last_call[0])
        _em_last_call[0] = start + _EM_MIN_INTERVAL + random.uniform(0.1, 0.5)
    wait = start - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    with market_session() as session:
        return session.get(url, params=params, headers=headers, timeout=timeout)


# ---------------------------------------------------------------------------
# 监管异动 · 交易所监控池与严重异常波动（东财移动端公开聚合）
# ---------------------------------------------------------------------------

_MONITOR_URL = "https://mobappconfig.securities.eastmoney.com/emcfg/stock_monitor.json"
_MONITOR_MARKET = {"1": "SH", "0": "SZ", "B": "BJ"}
_ANOMALY_BASE = "https://dycalchis.eastmoney.com/price-anomaly"
_ANOMALY_PARAMS = {
    "team": "h5", "product": "EastMoney", "client": "WAP",
    "version": "9001", "name": "WAP", "user": "123",
}
_ANOMALY_RULES = {
    1: "10个交易日内4次同向一般异动",
    2: "创业板10个交易日内3次同向一般异动",
    3: "科创板10个交易日内3次同向一般异动",
    4: "10个交易日累计上涨100%",
    5: "10个交易日累计下跌50%",
    6: "30个交易日累计上涨200%",
    7: "30个交易日累计下跌70%",
    8: "北交所10个交易日内3次同向一般异动",
    40: "10个交易日累计上涨150%",
    50: "10个交易日累计下跌60%",
    60: "30个交易日累计上涨300%",
    70: "30个交易日累计下跌75%",
}


def _normal_date(value) -> str:
    text = str(value or "").strip().replace("/", "-")
    if len(text) >= 10 and text[4:5] == "-":
        return text[:10]
    digits = re.sub(r"\D", "", text)
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) >= 8 else ""


def em_stock_monitor(only_active: bool = True) -> list[dict]:
    """交易所公开重点监控池。数据源拒绝/结构异常时显式抛错。"""
    response = em_get(_MONITOR_URL, headers={"User-Agent": UA, "Referer": "https://vipmoney.eastmoney.com/"}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(rows, dict):
        rows = rows.get("list") or rows.get("rows") or rows.get("data")
    if not isinstance(rows, list):
        raise RuntimeError("重点监控池数据源没有返回有效列表")
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    output = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("STKCODE") or raw.get("code") or raw.get("securityCode") or raw.get("c") or "").zfill(6)
        if not re.fullmatch(r"\d{6}", code):
            continue
        start = _normal_date(raw.get("VALIDATESTARTDATE") or raw.get("start") or raw.get("startDate") or raw.get("s"))
        end = _normal_date(raw.get("VALIDATEENDDATE") or raw.get("end") or raw.get("endDate") or raw.get("e"))
        if only_active and ((start and today < start) or (end and today > end)):
            continue
        market_raw = str(raw.get("MARKET") or raw.get("market") or raw.get("m") or "").upper()
        market = _MONITOR_MARKET.get(market_raw, f"?{market_raw}")
        output.append({
            "code": code,
            "name": str(raw.get("STKNAME") or raw.get("name") or raw.get("securityName") or raw.get("n") or code),
            "market": market,
            "start": start,
            "end": end,
            "reason": str(raw.get("reason") or raw.get("content") or raw.get("r") or "交易所重点监控"),
            "announcement_date": _normal_date(raw.get("noticeDate") or raw.get("date") or raw.get("d") or start),
            "link": str(raw.get("LINK_URL") or raw.get("link") or raw.get("url") or raw.get("u") or ""),
        })
    return output


def _anomaly_get(path: str, page_size: int = 500, page_no: int = 1, **extra) -> dict:
    params = {**_ANOMALY_PARAMS, "pageSize": str(page_size), "pageNo": str(page_no), **extra}
    response = em_get(f"{_ANOMALY_BASE}/{path.lstrip('/')}", params=params, headers={"User-Agent": UA, "Referer": "https://vipmoney.eastmoney.com/"}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("result") not in (0, "0"):
        raise RuntimeError(f"严重异常波动数据源拒绝请求：{payload.get('message') or payload.get('msg') or '未知错误'}")
    if payload.get("data") is None:
        raise RuntimeError("严重异常波动数据源未返回 data")
    return payload


def _anomaly_market(code: str, raw_market, board=None) -> str:
    if code.startswith(("43", "83", "87", "92")) or board == 8 or str(raw_market).upper() == "B":
        return "BJ"
    if code.startswith(("60", "68")) or str(raw_market) == "1":
        return "SH"
    return "SZ"


def em_price_anomaly(page_size: int = 200, page_no: int = 1) -> dict:
    payload = _anomaly_get("list", page_size, page_no)
    rows = payload.get("data") or []
    if not isinstance(rows, list):
        raise RuntimeError("严重异常波动列表结构异常")
    output = []
    for raw in rows:
        code = str(raw.get("c") or raw.get("code") or "").zfill(6)
        if not re.fullmatch(r"\d{6}", code):
            continue
        board = int(float(raw.get("s") or 0))
        base_rule = int(float(raw.get("e") or raw.get("ruleCode") or 0))
        occurrence = int(float(raw.get("o") or 0))
        rule_code = base_rule * 10 if board == 6 and base_rule in {4, 5, 6, 7} else base_rule
        output.append({
            "code": code, "name": str(raw.get("n") or raw.get("name") or code),
            "market": _anomaly_market(code, raw.get("m"), board),
            "change_pct": float(raw.get("a") or 0), "deviation": float(raw.get("x") or 0),
            "days": int(float(raw.get("d") or 0)), "board": board,
            # Eastmoney's public anomaly feed is already a tri-state result:
            # 0 = same-day prediction, 1 = triggered, 2 = next-day prediction.
            # ``t`` is the one-day change required to reach the condition.
            "occurrence": occurrence, "target_pct": float(raw.get("t") or 0),
            "rule_code": rule_code, "rule": _ANOMALY_RULES.get(rule_code, "严重异常波动"),
            "event_date": _normal_date(payload.get("date")),
            "is_today": occurrence != 2,
            "link": str(raw.get("u") or raw.get("url") or ""),
        })
    return {"date": str(payload.get("date") or ""), "pages": payload.get("pages", 0), "items": output}


def em_price_anomaly_count(page_size: int = 50, page_no: int = 1, sort_key: str = "", sort_dir: str = "") -> dict:
    payload = _anomaly_get("count", page_size, page_no, sortKey=sort_key, sortDir=sort_dir)
    rows = payload.get("data") or []
    if not isinstance(rows, list):
        raise RuntimeError("异动次数列表结构异常")
    output = []
    for raw in rows:
        code = str(raw.get("c") or raw.get("code") or "").zfill(6)
        if not re.fullmatch(r"\d{6}", code):
            continue
        output.append({
            "code": code, "name": str(raw.get("n") or raw.get("name") or code),
            "market": _anomaly_market(code, raw.get("m"), raw.get("s")), "price": float(raw.get("p") or 0),
            "change_pct": float(raw.get("a") or 0), "times": int(float(raw.get("t") or 0)),
            "deviation": float(raw.get("x") or 0), "days": int(float(raw.get("d") or 0)),
            "board": int(float(raw.get("s") or 0)),
        })
    return {"date": str(payload.get("date") or ""), "pages": payload.get("pages", 0), "items": output}


# ---------------------------------------------------------------------------
# 打板层 · 涨停/炸板/跌停/昨涨停 原始池（东财 push2ex，走 em_get 限流）
# ⚠️ 合规：原始池含个股 code/name —— 仅供 market.py 聚合成【不含个股名】的短线情绪指标。
#    切勿把原始池直接接成 API/UI（会甩个股名单、破产品「零标的」红线）。
# ---------------------------------------------------------------------------
_ZTB_UT = "7eea3edcaed734bea9cbfc24409ed989"

_ZT_TOPIC_SORT = {
    "getTopicZTPool": "fbt:asc",
    "getTopicZBPool": "fbt:asc",
    "getTopicDTPool": "fund:asc",
    "getYesterdayZTPool": "zs:desc",
}


def _zt_topic_sort(endpoint: str, sort: str | None) -> str:
    return sort or _ZT_TOPIC_SORT.get(endpoint, "fbt:asc")


def em_zt_topic_pool(endpoint: str, date: str, sort: str | None = None) -> list[dict]:
    """东财涨停板行情中心原始池（push2ex）。
    endpoint: getTopicZTPool(涨停) / getTopicZBPool(炸板) / getTopicDTPool(跌停) / getYesterdayZTPool(昨涨停)
    date: YYYYMMDD 交易日。非交易日 / 参数错 → []。
    池内每项字段含 lbc(连板数) / zbc(炸板次数) / hybk(行业) 等。"""
    url = f"https://push2ex.eastmoney.com/{endpoint}"
    params = {"ut": _ZTB_UT, "dpt": "wz.ztzt", "Pageindex": 0,
              "pagesize": 10000, "sort": _zt_topic_sort(endpoint, sort), "date": date}
    headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
    try:
        r = em_get(url, params=params, headers=headers, timeout=10)
        return (r.json().get("data") or {}).get("pool") or []
    except Exception:
        return []


def em_zt_topic_observed(endpoint: str, date: str, sort: str | None = None) -> dict:
    """严格区分数据源失败与真实零家，供复盘与首板等事实消费者使用。"""
    params = {"ut": _ZTB_UT, "dpt": "wz.ztzt", "Pageindex": 0,
              "pagesize": 10000, "sort": _zt_topic_sort(endpoint, sort), "date": date}
    try:
        response = em_get(
            f"https://push2ex.eastmoney.com/{endpoint}", params=params,
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}, timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data")
        if payload.get("rc", 0) != 0 or not isinstance(data, dict) or not isinstance(data.get("pool"), list):
            raise ValueError("数据源没有返回有效池（不能视为零家）")
        source_date = str(data.get("qdate") or date).replace("-", "")
        if source_date != date:
            raise ValueError("池数据所属日期不符")
        rows = data["pool"]
        if data.get("tc") is not None and int(data["tc"]) != len(rows):
            raise ValueError("池数据未完整返回")
        if any(not re.fullmatch(r"\d{6}", str(row.get("c", ""))) for row in rows):
            raise ValueError("池成员代码格式错误")
        return {"available": True, "rows": rows, "date": date,
                "source_date": data.get("qdate"), "reason": ""}
    except Exception as exc:
        return {"available": False, "rows": None, "date": date,
                "reason": f"{type(exc).__name__}: {exc}"}


def _numf(v):
    """东财数值字段可能是 '-'（停牌/无数据）→ 归一成 float 或 None。"""
    return v if isinstance(v, (int, float)) else None


def market_turnover_rank(n: int = 20) -> list[dict]:
    """全市场成交额榜（沪深京 A 股按成交额降序 TopN）。

    东财行情中心 clist。**push2(实时) 不可达时降级 push2delay(延迟行情，日榜场景足够)**。
    返回每只: code / name / price / pct / amount(成交额,元) / mcap(总市值,元) /
    float_cap(流通市值,元) / industry。
    ⚠️ 这是客观公开榜单数据（东财/同花顺同款），产品侧只做客观展示——非推荐、非预测、不评分。
    """
    params = {"pn": 1, "pz": n, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f6",
              "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
              "fields": "f12,f14,f2,f3,f6,f20,f21,f100"}
    diff: list[dict] = []
    for host in ("push2.eastmoney.com", "push2delay.eastmoney.com"):
        try:
            r = em_get(f"https://{host}/api/qt/clist/get", params=params,
                       headers={"User-Agent": UA}, timeout=12)
            diff = (r.json().get("data") or {}).get("diff") or []
            if diff:
                break
        except Exception:
            continue
    return [{
        "code": str(d.get("f12", "")), "name": d.get("f14", ""),
        "price": _numf(d.get("f2")), "pct": _numf(d.get("f3")),
        "amount": _numf(d.get("f6")), "mcap": _numf(d.get("f20")),
        "float_cap": _numf(d.get("f21")), "industry": d.get("f100", "") or "",
    } for d in diff]


def eastmoney_datacenter(report_name: str, columns: str = "ALL", filter_str: str = "",
                         page_size: int = 50, sort_columns: str = "", sort_types: str = "-1",
                         *, page_number: int = 1, strict: bool = False) -> list[dict]:
    """东财数据中心统一查询 —— 龙虎榜/解禁/融资融券/大宗交易/股东户数/分红 共用（已内置限流）。"""
    params = {
        "reportName": report_name, "columns": columns, "filter": filter_str,
        "pageNumber": str(page_number), "pageSize": str(page_size),
        "sortColumns": sort_columns, "sortTypes": sort_types, "source": "WEB", "client": "WEB",
    }
    try:
        d = em_get(_DATACENTER_URL, params=params, timeout=15).json()
        if not strict:
            return (d.get("result") or {}).get("data") or []
        # Eastmoney explicitly reports an empty query as code 9201.
        if (isinstance(d, dict) and d.get("code") == 9201 and d.get("success") is False
                and d.get("message") == "返回数据为空" and d.get("result") is None):
            return []
        if not isinstance(d, dict) or d.get("success") is not True:
            raise ValueError("invalid data-center response")
        result = d.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise ValueError("missing data-center rows")
        return result["data"]
    except Exception as exc:
        if strict:
            raise RuntimeError("东财数据中心取数失败，无法确认该查询是否确实为空；请稍后重试") from exc
        return []


def margin_trading(code: str, page_size: int = 30) -> list[dict]:
    """融资融券明细（日级）：融资余额 / 融资买入 / 融券余额 / 两融合计。"""
    data = eastmoney_datacenter(
        "RPTA_WEB_RZRQ_GGMX", filter_str=f'(SCODE="{code}")',
        page_size=page_size, sort_columns="DATE", sort_types="-1")
    return [{
        "date": str(r.get("DATE", ""))[:10],
        "rzye": r.get("RZYE", 0), "rzmre": r.get("RZMRE", 0), "rzche": r.get("RZCHE", 0),
        "rqye": r.get("RQYE", 0), "rqmcl": r.get("RQMCL", 0),
        "rzrqye": r.get("RZRQYE", 0),
    } for r in data]


def block_trade(code: str, page_size: int = 20) -> list[dict]:
    """大宗交易：成交价 / 折溢价率 / 量 / 买卖方营业部。"""
    data = eastmoney_datacenter(
        "RPT_DATA_BLOCKTRADE", filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size, sort_columns="TRADE_DATE", sort_types="-1")
    rows = []
    for r in data:
        close = r.get("CLOSE_PRICE") or 0
        deal = r.get("DEAL_PRICE") or 0
        rows.append({
            "date": str(r.get("TRADE_DATE", ""))[:10],
            "price": deal, "close": close,
            "premium_pct": round((deal / close - 1) * 100, 2) if close else 0,
            "vol": r.get("DEAL_VOLUME", 0), "amount": r.get("DEAL_AMT", 0),
            "buyer": r.get("BUYER_NAME", ""), "seller": r.get("SELLER_NAME", ""),
        })
    return rows


def holder_num_change(code: str, page_size: int = 10) -> list[dict]:
    """股东户数变化（季度级）：户数 / 环比 / 户均持股。持续减少 = 筹码集中。"""
    data = eastmoney_datacenter(
        "RPT_HOLDERNUMLATEST", filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size, sort_columns="END_DATE", sort_types="-1")
    return [{
        "date": str(r.get("END_DATE", ""))[:10],
        "holder_num": r.get("HOLDER_NUM", 0),
        "change_ratio": r.get("HOLDER_NUM_RATIO", 0),
        "avg_shares": r.get("AVG_FREE_SHARES", 0),
    } for r in data]


def dividend_history(code: str, page_size: int = 20) -> list[dict]:
    """分红送转历史：每股派息（税前）/ 每10股转增 / 每10股送股 / 进度。"""
    data = eastmoney_datacenter(
        "RPT_SHAREBONUS_DET", filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size, sort_columns="EX_DIVIDEND_DATE", sort_types="-1")
    return [{
        "date": str(r.get("EX_DIVIDEND_DATE", ""))[:10],
        "bonus_rmb": r.get("PRETAX_BONUS_RMB", 0),
        "transfer_ratio": r.get("TRANSFER_RATIO", 0),
        "bonus_ratio": r.get("BONUS_RATIO", 0),
        "plan": r.get("ASSIGN_PROGRESS", ""),
    } for r in data]


def stock_fund_flow_120d(code: str) -> list[dict]:
    """个股资金流（日级，最近 120 交易日）：主力 / 小单 / 中单 / 大单 / 超大单净流入（元）。"""
    market_code = 1 if code.startswith("6") else 0
    params = {
        "secid": f"{market_code}.{code}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "lmt": "120",
    }
    headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/", "Origin": "https://quote.eastmoney.com"}
    try:
        d = em_get("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
                   params=params, headers=headers, timeout=15).json()
    except Exception:
        return []
    rows = []
    for line in d.get("data", {}).get("klines", []):
        p = line.split(",")
        if len(p) >= 6:
            def _f(x):
                try:
                    return float(x) if x not in ("-", "") else 0.0
                except ValueError:
                    return 0.0
            rows.append({
                "date": p[0], "main_net": _f(p[1]), "small_net": _f(p[2]),
                "mid_net": _f(p[3]), "large_net": _f(p[4]), "super_net": _f(p[5]),
            })
    return rows


def dragon_tiger_board(code: str, trade_date: str | None = None, look_back: int = 30) -> dict:
    """龙虎榜：该股近期上榜记录 + 最近一次买卖席位 TOP5 + 机构专用席位净买。"""
    trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
    start = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=look_back)).strftime("%Y-%m-%d")
    records = []
    data = eastmoney_datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str=f'(TRADE_DATE>=\'{start}\')(TRADE_DATE<=\'{trade_date}\')(SECURITY_CODE="{code}")',
        page_size=50, sort_columns="TRADE_DATE", sort_types="-1")
    for r in data:
        records.append({
            "date": str(r.get("TRADE_DATE", ""))[:10],
            "reason": r.get("EXPLANATION", ""),
            "net_buy": round((r.get("BILLBOARD_NET_AMT") or 0) / 10000, 1),  # 万元
            "turnover": round(float(r.get("TURNOVERRATE") or 0), 2),
        })

    seats = {"buy": [], "sell": []}
    institution = {"buy_amt": 0.0, "sell_amt": 0.0, "net_amt": 0.0}
    if records:
        latest = records[0]["date"]
        buy_data = eastmoney_datacenter(
            "RPT_BILLBOARD_DAILYDETAILSBUY",
            filter_str=f'(TRADE_DATE=\'{latest}\')(SECURITY_CODE="{code}")',
            page_size=10, sort_columns="BUY", sort_types="-1")
        sell_data = eastmoney_datacenter(
            "RPT_BILLBOARD_DAILYDETAILSSELL",
            filter_str=f'(TRADE_DATE=\'{latest}\')(SECURITY_CODE="{code}")',
            page_size=10, sort_columns="SELL", sort_types="-1")
        for r in buy_data[:5]:
            seats["buy"].append({"name": r.get("OPERATEDEPT_NAME", ""),
                                 "buy_amt": round((r.get("BUY") or 0) / 10000, 1),
                                 "sell_amt": round((r.get("SELL") or 0) / 10000, 1),
                                 "net": round((r.get("NET") or 0) / 10000, 1)})
        for r in sell_data[:5]:
            seats["sell"].append({"name": r.get("OPERATEDEPT_NAME", ""),
                                  "buy_amt": round((r.get("BUY") or 0) / 10000, 1),
                                  "sell_amt": round((r.get("SELL") or 0) / 10000, 1),
                                  "net": round((r.get("NET") or 0) / 10000, 1)})
        for detail, side in ((buy_data, "buy"), (sell_data, "sell")):
            for r in detail:
                if str(r.get("OPERATEDEPT_CODE", "")) == "0":  # 机构专用席位
                    amt = (r.get("BUY") or 0) if side == "buy" else (r.get("SELL") or 0)
                    institution[f"{side}_amt"] += amt
        institution["buy_amt"] = round(institution["buy_amt"] / 10000, 1)
        institution["sell_amt"] = round(institution["sell_amt"] / 10000, 1)
        institution["net_amt"] = round(institution["buy_amt"] - institution["sell_amt"], 1)
    return {"records": records, "seats": seats, "institution": institution}


def _datacenter_all(report_name: str, filter_str: str, *, sort_columns: str,
                    sort_types: str = "-1", page_size: int = 500,
                    max_pages: int = 5) -> list[dict]:
    """Read a complete small day-level Eastmoney report without parallel requests."""
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        batch = eastmoney_datacenter(
            report_name, filter_str=filter_str, page_size=page_size,
            sort_columns=sort_columns, sort_types=sort_types,
            page_number=page, strict=True,
        )
        rows.extend(batch)
        if len(batch) < page_size:
            break
    else:
        raise RuntimeError("龙虎榜数据超过分页上限，未返回不完整结果")
    return rows


def _finite(value, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _branch_profile(name: str) -> dict:
    """Return factual or explicitly qualified public-market seat labels.

    Institution/connect labels are exchange facts. Quant/hot-money/retail
    labels are public-market branch profiles, not proof of the account that
    placed this specific trade, so the description always preserves that
    distinction for the UI tooltip.
    """
    if "机构专用" in name:
        return {"category": "institution", "tags": ["机构专用"],
                "description": "交易所披露的机构专用席位。"}
    for label in ("沪股通专用", "深股通专用"):
        if label in name:
            return {"category": "northbound", "tags": [label],
                    "description": f"交易所披露的{label}席位。"}

    normalized = re.sub(r"股份有限公司|有限责任公司|有限公司|证券营业部|营业部|\s+", "", name)
    profiles = (
        (("国泰海通证券总部", "国泰君安证券总部", "中信证券上海分公司", "国信证券浙江互联网分公司"),
         "quant", ["量化基金"], "公开市场常见量化席位标签；不等于本次交易账户身份已被确认。"),
        (("国盛证券宁波桑田路",),
         "hot_money", ["游资", "宁波桑田路"], "公开市场常见游资席位别名；不等于本次交易由对应个人或团队完成。"),
        (("国泰海通证券北京知春路", "国泰君安证券北京知春路"),
         "hot_money", ["游资", "北京知春路"], "公开市场常见游资席位别名；不等于本次交易由对应个人或团队完成。"),
        (("东方财富证券拉萨",),
         "retail", ["散户席位", "拉萨天团"], "公开市场常见散户集中型席位标签；不代表单一账户或一致行动。"),
    )
    for patterns, category, tags, description in profiles:
        if any(pattern in normalized for pattern in patterns):
            return {"category": category, "tags": tags, "description": description}
    return {"category": "brokerage", "tags": [],
            "description": "未匹配到可靠公开标签，按普通营业部展示。"}


def _branch_row(row: dict, total_deal: float, side: str, position: int = 0) -> dict:
    buy, sell, net = (_finite(row.get(key)) for key in ("BUY", "SELL", "NET"))
    ratio_key = "TOTAL_BUYRIO" if side == "buy" else "TOTAL_SELLRIO"
    side_amount = buy if side == "buy" else sell
    raw_ratio = row.get(ratio_key)
    ratio = _finite(raw_ratio, -1.0)
    if raw_ratio is None or ratio < 0:
        ratio = side_amount / total_deal * 100 if total_deal else 0.0
    elif ratio <= 1:
        ratio *= 100  # Eastmoney TOTAL_*RIO is normally a decimal fraction.
    name = str(row.get("OPERATEDEPT_NAME") or "未知营业部").strip()
    profile = _branch_profile(name)
    seat_id = "|".join(str(value or "") for value in (
        row.get("TRADE_ID"), row.get("OPERATEDEPT_CODE"),
        row.get("OPERATEDEPT_CODE_OLD"), side, round(side_amount, 2), position,
    ))
    return {
        "seat_id": seat_id,
        "name": name,
        "buy_wan": round(buy / 10000, 2),
        "sell_wan": round(sell / 10000, 2),
        "net_wan": round(net / 10000, 2),
        "ratio": round(ratio, 2),
        **profile,
    }


def daily_dragon_tiger(trade_date: str) -> dict:
    """Full-market daily Dragon-Tiger list with the published branch details.

    Eastmoney repeats a stock when it triggers multiple listing rules. Those
    rows share the same totals, so this function merges the reasons and keeps
    one monetary snapshot instead of incorrectly summing duplicate totals.
    Money in the returned contract is uniformly expressed in ten-thousand yuan.
    """
    try:
        day = datetime.strptime(str(trade_date), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("date 必须是 YYYY-MM-DD") from exc
    day_filter = f"(TRADE_DATE='{day}')"
    detail_rows = _datacenter_all(
        "RPT_DAILYBILLBOARD_DETAILSNEW", day_filter,
        sort_columns="BILLBOARD_NET_AMT", sort_types="-1",
    )

    grouped: dict[str, dict] = {}
    for row in detail_rows:
        code = str(row.get("SECURITY_CODE") or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            continue
        item = grouped.setdefault(code, {"rows": [], "reasons": []})
        item["rows"].append(row)
        reason = str(row.get("EXPLANATION") or "").strip()
        if reason and reason not in item["reasons"]:
            item["reasons"].append(reason)

    if not grouped:
        return {
            "available": True, "date": day, "total_records": len(detail_rows),
            "total_stocks": 0, "source": "eastmoney_datacenter",
            "reason_note": None, "branch_note": None, "stocks": [],
        }

    buy_rows: list[dict] = []
    sell_rows: list[dict] = []
    branch_note = None
    if grouped:
        try:
            buy_rows = _datacenter_all(
                "RPT_BILLBOARD_DAILYDETAILSBUY", day_filter,
                sort_columns="BUY", sort_types="-1",
            )
            sell_rows = _datacenter_all(
                "RPT_BILLBOARD_DAILYDETAILSSELL", day_filter,
                sort_columns="SELL", sort_types="-1",
            )
        except RuntimeError as exc:
            branch_note = f"营业部明细暂不可用：{exc}"

    branch_rows: dict[str, dict[str, list[dict]]] = {
        code: {"buy": [], "sell": []} for code in grouped
    }
    for side, rows in (("buy", buy_rows), ("sell", sell_rows)):
        for row in rows:
            code = str(row.get("SECURITY_CODE") or "").strip()
            if code not in grouped:
                continue
            branch_rows[code][side].append(row)

    reason_map: dict[str, str] = {}
    reason_note = None
    try:
        from duanxian.theme_reasons import get_reasons
        reason_map, reason_note = get_reasons(day)
    except Exception as exc:  # optional enrichment; the official list remains usable
        reason_note = f"题材标签暂不可用（{type(exc).__name__}）"

    board_by_code = {}
    try:
        board_by_code = {str(row.get("c") or ""): row for row in em_zt_topic_pool(
            "getTopicZTPool", day.replace("-", ""),
        )}
    except Exception:
        board_by_code = {}

    stocks = []
    for code, group in grouped.items():
        rows = group["rows"]
        row = max(rows, key=lambda record: abs(_finite(record.get("BILLBOARD_NET_AMT"))))
        deal = _finite(row.get("BILLBOARD_DEAL_AMT"))
        theme = str(reason_map.get(code) or "").strip()
        concept = next((part.strip() for part in re.split(r"[+｜|、]", theme) if part.strip()), None)
        pool = board_by_code.get(code) or {}
        boards = int(_finite(pool.get("lbc")))
        board_label = f"{boards}连板" if boards >= 2 else ("首板" if boards == 1 else None)

        branches = {}
        for side in ("buy", "sell"):
            amount_key = "BUY" if side == "buy" else "SELL"
            candidates = branch_rows[code][side]
            trade_id = str(row.get("TRADE_ID") or "")
            if trade_id:
                same_record = [branch for branch in candidates if str(branch.get("TRADE_ID") or "") == trade_id]
                if same_record:
                    candidates = same_record
            # Keep the official five rows. Repeated “机构专用” entries are
            # separate disclosed seats and must not be collapsed by name.
            selected = sorted(candidates, key=lambda branch: _finite(branch.get(amount_key)), reverse=True)[:5]
            branches[side] = [
                _branch_row(branch, deal, side, position)
                for position, branch in enumerate(selected, start=1)
            ]
        stocks.append({
            "code": code,
            "name": str(row.get("SECURITY_NAME_ABBR") or code),
            "close": _finite(row.get("CLOSE_PRICE")),
            "change_pct": round(_finite(row.get("CHANGE_RATE")), 2),
            "turnover_pct": round(_finite(row.get("TURNOVERRATE")), 2),
            "deal_wan": round(deal / 10000, 2),
            "buy_wan": round(_finite(row.get("BILLBOARD_BUY_AMT")) / 10000, 2),
            "sell_wan": round(_finite(row.get("BILLBOARD_SELL_AMT")) / 10000, 2),
            "net_wan": round(_finite(row.get("BILLBOARD_NET_AMT")) / 10000, 2),
            "reasons": group["reasons"],
            "reason": "；".join(group["reasons"]),
            "concept": concept,
            "theme_reason": theme or None,
            "board_label": board_label,
            "buy_branches": branches["buy"],
            "sell_branches": branches["sell"],
        })
    stocks.sort(key=lambda stock: abs(stock["net_wan"]), reverse=True)
    return {
        "available": True,
        "date": day,
        "total_records": len(detail_rows),
        "total_stocks": len(stocks),
        "source": "eastmoney_datacenter",
        "reason_note": reason_note,
        "branch_note": branch_note,
        "stocks": stocks,
    }


# ---------------------------------------------------------------------------
# More ladders · consecutive bullish-candle ladder
# ---------------------------------------------------------------------------

def _growth_field(row: dict, *needles: str, prefer_date: str = ""):
    """Read iwencai/THS columns whose names carry a changing date suffix."""
    matches = []
    for key, value in row.items():
        label = str(key).replace(" ", "").lower()
        if any(needle.replace(" ", "").lower() in label for needle in needles):
            matches.append((str(key), value))
    if prefer_date:
        compact = prefer_date.replace("-", "")
        dated = [item for item in matches if compact in item[0].replace("-", "")]
        if dated:
            return dated[0][1]
    return matches[0][1] if matches else None


def _growth_number(value, default: float = 0.0) -> float:
    if isinstance(value, str):
        match = re.search(r"[-+]?\d+(?:\.\d+)?", value.replace(",", ""))
        value = match.group(0) if match else None
    return _finite(value, default)


def _growth_pct(row: dict, trade_date: str) -> float:
    """Prefer a one-day percentage and never mistake a cumulative move for it."""
    compact = trade_date.replace("-", "")
    candidates = []
    for key, value in row.items():
        label = str(key).replace(" ", "")
        if "涨跌幅" not in label and label not in {"涨幅", "最新涨幅"}:
            continue
        if any(word in label for word in ("连续", "累计", "区间", "阶段")):
            continue
        score = (4 if compact and compact in label.replace("-", "") else 0)
        score += 2 if "最新" in label else 0
        score += 1 if label in {"涨跌幅", "涨幅", "最新涨跌幅", "最新涨幅"} else 0
        candidates.append((score, value))
    if not candidates:
        return 0.0
    return _growth_number(max(candidates, key=lambda item: item[0])[1])


def _growth_industry(value) -> str:
    if isinstance(value, (list, tuple)):
        parts = [str(part).strip() for part in value if str(part).strip()]
        return (parts[1] if len(parts) > 1 else (parts[0] if parts else "未知"))
    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        parts = [part.strip(" '\"") for part in text[1:-1].split(",") if part.strip(" '\"")]
        if parts:
            return parts[1] if len(parts) > 1 else parts[0]
    if "-" in text:
        parts = [part.strip() for part in text.split("-") if part.strip()]
        if len(parts) > 1:
            return parts[1]
    return text or "未知"


def _growth_items_from_rows(rows: list[dict], trade_date: str) -> list[dict]:
    """Normalize date-suffixed iwencai rows into the stable UI contract."""
    items: dict[str, dict] = {}
    for row in rows:
        code_raw = _growth_field(row, "股票代码", "证券代码", "代码")
        match = re.search(r"(?<!\d)(\d{6})(?!\d)", str(code_raw or ""))
        if not match:
            continue
        code = match.group(1)
        positive_level = int(_growth_number(_growth_field(
            row, "连续收阳天数", "连续阳线天数", "连阳天数",
            prefer_date=trade_date,
        )))
        rising_level = int(_growth_number(_growth_field(
            row, "连续上涨天数", "连续收涨天数", "连涨天数",
            prefer_date=trade_date,
        )))
        # QuickTiny's ladder uses the longer of consecutive close-price rises
        # and consecutive non-bearish candles, rather than only one streak.
        level = max(positive_level, rising_level)
        if level < 3:
            continue
        name = str(_growth_field(row, "股票简称", "证券简称", "股票名称", "名称") or code).strip()
        industry = _growth_industry(_growth_field(
            row, "所属同花顺行业", "所属行业", "行业",
            prefer_date=trade_date,
        ))
        pct = _growth_pct(row, trade_date)
        high_value = _growth_field(row, "阶段新高", "创120日新高", "创新高", prefer_date=trade_date)
        high_text = str(high_value or "").strip().lower()
        stage_high = bool(high_value) and high_text not in {"0", "0.0", "false", "否", "无", "nan", "none", "-"}
        items[code] = {
            "code": code, "name": name, "industry": industry or "未知",
            "pct_chg": round(pct, 2), "level": level,
            "eliminated": False, "stage_high": stage_high,
        }
    return list(items.values())


def _iwencai_growth_rows(trade_date: str) -> list[dict]:
    """Fetch a narrow candidate union without the timeout-prone OR query."""
    from duanxian.data_credentials import apply_iwencai_override

    if not apply_iwencai_override():
        raise DependencyMissing("未配置 IWENCAI_API_KEY")
    from iwencai_client import IwencaiClient

    datetime.strptime(trade_date, "%Y-%m-%d")
    client = IwencaiClient(timeout=30, max_retries=2)
    merged: dict[str, dict] = {}
    errors = []
    for metric in ("连续上涨天数", "连续收阳天数"):
        query = (
            f"{trade_date}{metric}大于等于3，非ST，"
            "列出股票代码、股票简称、所属同花顺行业、涨跌幅"
        )
        try:
            for frame in client.query_iter(query, page_size=100, max_pages=60):
                for row in frame.to_dict("records"):
                    code_raw = _growth_field(row, "股票代码", "证券代码", "代码")
                    match = re.search(r"(?<!\d)(\d{6})(?!\d)", str(code_raw or ""))
                    if not match:
                        continue
                    code = match.group(1)
                    merged.setdefault(code, {}).update(row)
        except Exception as exc:
            errors.append(exc)
    if not merged and errors:
        raise errors[-1]
    return list(merged.values())


def _growth_streak_at(rows: list[dict], trade_date: str) -> dict | None:
    """Recompute QuickTiny's two streaks from daily bars at one session."""
    normalized = []
    for row in rows:
        day = str(row.get("datetime") or row.get("date") or "")[:10]
        if not day:
            continue
        try:
            normalized.append({
                "date": day,
                "open": float(row.get("open")),
                "close": float(row.get("close")),
            })
        except (TypeError, ValueError):
            continue
    normalized.sort(key=lambda item: item["date"])
    index = next((position for position, item in enumerate(normalized) if item["date"] == trade_date), None)
    if index is None:
        return None

    positive_level = 0
    for position in range(index, -1, -1):
        item = normalized[position]
        previous_close = normalized[position - 1]["close"] if position > 0 else None
        is_positive = item["close"] > item["open"]
        is_non_falling_doji = (
            math.isclose(item["close"], item["open"], abs_tol=1e-8)
            and previous_close is not None
            and item["close"] >= previous_close
        )
        if not (is_positive or is_non_falling_doji):
            break
        positive_level += 1

    rising_level = 0
    for position in range(index, 0, -1):
        if normalized[position]["close"] <= normalized[position - 1]["close"]:
            break
        rising_level += 1

    close = normalized[index]["close"]
    previous_close = normalized[index - 1]["close"] if index > 0 else None
    pct = ((close / previous_close - 1) * 100) if previous_close else 0.0
    window = normalized[max(0, index - 119):index + 1]
    stage_high = bool(window) and close >= max(item["close"] for item in window)
    return {
        "positive_level": positive_level,
        "rising_level": rising_level,
        "level": max(positive_level, rising_level),
        "pct_chg": round(pct, 2),
        "stage_high": stage_high,
    }


def _growth_histories(codes: list[str]) -> tuple[dict[str, list[dict]], list[str]]:
    """Load enough bars once for both selected and previous-session recomputation."""
    unique = sorted(set(codes))
    histories: dict[str, list[dict]] = {}
    failed: list[str] = []
    if not unique:
        return histories, failed
    with ThreadPoolExecutor(max_workers=min(32, len(unique))) as executor:
        futures = {executor.submit(_tencent_daily_kline, code, 180): code for code in unique}
        for future in as_completed(futures):
            code = futures[future]
            try:
                rows = future.result()
                if rows:
                    histories[code] = rows
                else:
                    failed.append(code)
            except Exception:
                failed.append(code)
    return histories, failed


def _ths_growth_fallback(trade_date: str) -> list[dict]:
    """Public THS consecutive-rise ranking, used only as an explicit approximation.

    The public ranking is current-day only and measures consecutive price rises,
    not every positive candle. Keeping it separate prevents the fallback from
    being presented as the exact historical bullish-candle universe.
    """
    from duanxian.trade_calendar import latest_session

    if trade_date != latest_session():
        return []
    ak = _akshare()
    frame = ak.stock_rank_lxsz_ths()
    if frame is None or getattr(frame, "empty", True):
        return []
    return frame.to_dict("records")


def _growth_quotes(codes: list[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for start in range(0, len(codes), 50):
        try:
            result.update(tencent_quote(codes[start:start + 50]))
        except Exception:
            continue
    return result


def daily_growth_ladder(trade_date: str) -> dict:
    """Read a strictly covered, locally computed full-market snapshot."""
    from duanxian.growth_ladders import get_ladder
    return get_ladder(trade_date)


def _legacy_daily_growth_ladder(trade_date: str) -> dict:
    """Legacy candidate-based implementation retained for data-source diagnostics only."""
    try:
        day = datetime.strptime(str(trade_date), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("date 必须是 YYYY-MM-DD") from exc
    from duanxian.trade_calendar import prev_trade_date, trade_dates_ending_at

    previous = prev_trade_date(day)
    exact = True
    source = "iwencai_openapi"
    note = ""
    try:
        active_rows = _iwencai_growth_rows(day)
        previous_rows = _iwencai_growth_rows(previous) if previous else []
    except Exception as exc:
        exact = False
        source = "ths_continuous_rise_fallback"
        previous_rows = []
        try:
            active_rows = _ths_growth_fallback(day)
        except Exception as fallback_exc:
            reason = (
                f"精确连阳源不可用（{type(exc).__name__}: {exc}）；"
                f"公开连续上涨榜也不可用（{type(fallback_exc).__name__}: {fallback_exc}）"
            )
            return {
                "available": False, "date": day, "live": False,
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "rule": (
                    "收盘连涨或连续收阳满 3 天进入天梯，按两者中较长的分层；"
                    "开盘等于收盘且不跌也算收阳。收盘创近 120 日新高的单独标出；"
                    "昨天不少于 5 天、今天断掉的标成断阳。"
                ),
                "source": source, "coverage": "unavailable", "note": reason,
                "available_dates": trade_dates_ending_at(day, 20), "levels": [],
            }
        note = (
            "未取得问财精确连阳结果，当前降级为同花顺公开连续上涨榜；"
            "它按连续上涨而非逐根阳线统计，不包含断阳，仅作近似展示。"
        )

    active_candidates = _growth_items_from_rows(active_rows, day)
    prior_candidates = _growth_items_from_rows(previous_rows, previous or day)
    active = active_candidates
    prior = prior_candidates
    broken = []

    if exact:
        metadata = {item["code"]: item for item in prior_candidates}
        metadata.update({item["code"]: item for item in active_candidates})
        histories, failed = _growth_histories(list(metadata))

        active = []
        for code, item in metadata.items():
            stats = _growth_streak_at(histories.get(code, []), day)
            if stats and stats["level"] >= 3:
                active.append({
                    **item,
                    "level": stats["level"],
                    "pct_chg": stats["pct_chg"],
                    "stage_high": stats["stage_high"],
                    "eliminated": False,
                })
        prior = []
        if previous:
            for item in prior_candidates:
                stats = _growth_streak_at(histories.get(item["code"], []), previous)
                if stats and stats["level"] >= 3:
                    prior.append({
                        **item,
                        "level": stats["level"],
                        "pct_chg": stats["pct_chg"],
                        "stage_high": stats["stage_high"],
                    })
        active_codes = {item["code"] for item in active}
        for item in prior:
            if item["level"] < 5 or item["code"] in active_codes:
                continue
            current_stats = _growth_streak_at(histories.get(item["code"], []), day)
            broken.append({
                **item,
                "pct_chg": current_stats["pct_chg"] if current_stats else 0.0,
                "eliminated": True,
                "stage_high": False,
            })
        source = "iwencai_candidates+tencent_daily_kline"
        if failed:
            note = f"{len(failed)} 只候选股日线暂时不可用，已跳过；刷新后会重新计算。"
    else:
        histories, failed = _growth_histories([item["code"] for item in active])
        recomputed = []
        for item in active:
            stats = _growth_streak_at(histories.get(item["code"], []), day)
            if stats and stats["level"] >= 3:
                recomputed.append({
                    **item,
                    "level": stats["level"],
                    "pct_chg": stats["pct_chg"],
                    "stage_high": stats["stage_high"],
                })
            elif not stats:
                recomputed.append(item)
        active = recomputed
        active_codes = {item["code"] for item in active}
        broken = [{**item, "eliminated": True, "stage_high": False}
                  for item in prior
                  if item["level"] >= 5 and item["code"] not in active_codes]
        if histories:
            source = "ths_continuous_rise+tencent_daily_kline"
            note = (
                "问财候选源当前不可用；在同花顺公开连续上涨候选上已按真实日K重算连阳层级、"
                "当日涨跌幅和120日新高。公开源不提供上一交易日完整候选，因此本次不展示断阳，"
                "避免把不完整结果当成准确数据。"
            )

    by_level: dict[int, list[dict]] = {}
    for item in active + broken:
        by_level.setdefault(int(item["level"]), []).append(item)
    levels = []
    for level in sorted(by_level, reverse=True):
        items = sorted(
            by_level[level],
            key=lambda item: (item["eliminated"], -item["pct_chg"], item["name"]),
        )
        levels.append({"level": level, "items": items, "overflow": 0})

    return {
        "available": True, "date": day, "live": False,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rule": (
            "收盘连涨或连续收阳满 3 天进入天梯，按两者中较长的分层；"
            "开盘等于收盘且不跌也算收阳。收盘创近 120 日新高的单独标出；"
            "昨天不少于 5 天、今天断掉的标成断阳。"
        ),
        "source": source, "coverage": "full" if exact and not note else ("partial" if exact else "approximate"),
        "note": note, "available_dates": trade_dates_ending_at(day, 20),
        "levels": levels,
    }


def _unlock_row(r: dict) -> dict:
    day = str(r.get("FREE_DATE", ""))[:10]
    datetime.strptime(day, "%Y-%m-%d")
    def number(key):
        value = r.get(key)
        if value is None:
            return None
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("解禁数据数值无效")
        return value
    ratio = number("TOTAL_RATIO")
    return {"date": day, "type": r.get("FREE_SHARES_TYPE", ""),
            "shares": number("CURRENT_FREE_SHARES"), "able_shares": number("ABLE_FREE_SHARES"),
            "ratio": None if ratio is None else ratio * 100}


def lockup_expiry(code: str, trade_date: str | None = None, forward_days: int = 90) -> dict:
    """Dated unlock batches. Shares in 10,000 shares; ratio in percent of total equity."""
    from duanxian.util import china_now
    trade_date = trade_date or china_now().strftime("%Y-%m-%d")
    start = datetime.strptime(trade_date, "%Y-%m-%d")
    if not re.fullmatch(r"\d{6}", code) or not 0 <= forward_days <= 366:
        raise ValueError("解禁查询需要六位代码及 0–366 天范围")
    end = (start + timedelta(days=forward_days)).strftime("%Y-%m-%d")

    def rows(filter_str, size, order):
        records = eastmoney_datacenter("RPT_LIFT_STAGE", filter_str=filter_str,
            page_size=size, sort_columns="FREE_DATE", sort_types=order, strict=True)
        result = []
        for r in records:
            day = str(r.get("FREE_DATE", ""))[:10]
            datetime.strptime(day, "%Y-%m-%d")
            if (order == "-1" and day >= trade_date) or (order == "1" and not trade_date <= day <= end):
                raise ValueError("解禁数据日期与查询范围不符，未使用该批数据")
            result.append(_unlock_row(r))
        return result

    history = rows(f'(SECURITY_CODE="{code}")(FREE_DATE<\'{trade_date}\')', 15, "-1")
    upcoming = rows(f'(SECURITY_CODE="{code}")(FREE_DATE>=\'{trade_date}\')(FREE_DATE<=\'{end}\')', 100, "1")
    return {"history": history, "upcoming": upcoming}


def lockup_calendar(window: str = "upcoming", trade_date: str | None = None) -> dict:
    """Ten calendar days, inclusive; watchlist matching stays in the browser."""
    from duanxian.util import china_now
    now = china_now()
    today = datetime.strptime(trade_date or now.strftime("%Y-%m-%d"), "%Y-%m-%d")
    if window not in {"upcoming", "recent"}:
        raise ValueError("请选择未来或最近十天")
    start = (today if window == "upcoming" else today - timedelta(days=9)).strftime("%Y-%m-%d")
    end = (today + timedelta(days=9) if window == "upcoming" else today).strftime("%Y-%m-%d")
    records = eastmoney_datacenter("RPT_LIFT_STAGE",
        filter_str=f"(FREE_DATE>='{start}')(FREE_DATE<='{end}')",
        page_size=500, sort_columns="FREE_DATE,SECURITY_CODE", sort_types="1,1", strict=True)
    events = []
    for row in records:
        event = _unlock_row(row)
        code = str(row.get("SECURITY_CODE", ""))
        if not re.fullmatch(r"\d{6}", code) or not start <= event["date"] <= end:
            raise ValueError("解禁记录代码或日期与查询范围不符")
        events.append({**event, "code": code, "name": row.get("SECURITY_NAME_ABBR", "")})
    return {"start": start, "end": end, "events": events, "truncated": len(events) >= 500,
            "fetched_at": now.isoformat()}


def concept_blocks(code: str) -> dict:
    """个股所属板块/概念归属（东财 slist，行业/概念/地域混合，板块名自解释）。"""
    market_code = 1 if code.startswith("6") else 0
    params = {"fltt": "2", "invt": "2", "secid": f"{market_code}.{code}",
              "spt": "3", "pi": "0", "pz": "200", "po": "1", "fields": "f12,f14,f3,f128"}
    headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
    d = None
    # 同一 slist 算法只切换东财官方镜像。住宅 IP/Clash 对 push2 偶发 reset
    # 时，不能把一次连接失败伪装成“该股没有概念”。
    for host in ("push2.eastmoney.com", "push2test.eastmoney.com", "push2delay.eastmoney.com"):
        try:
            candidate = em_get(f"https://{host}/api/qt/slist/get", params=params, headers=headers, timeout=15).json()
            if ((candidate.get("data") or {}).get("diff")):
                d = candidate
                break
        except Exception:
            continue
    if d is None:
        return {"total": 0, "boards": [], "concept_tags": []}
    diff = (d.get("data") or {}).get("diff") or {}
    items = diff.values() if isinstance(diff, dict) else diff
    boards = [{"name": it.get("f14", ""), "code": it.get("f12", ""),
               "change_pct": it.get("f3", ""), "lead_stock": it.get("f128", "")} for it in items]
    return {"total": len(boards), "boards": boards, "concept_tags": [b["name"] for b in boards]}


def hot_concepts(code: str) -> list[dict]:
    """个股当下被市场归到哪些概念在炒（东财热门概念命中，按热度降序）。"""
    try:
        prefix = "SH" if code.startswith("6") else "SZ"
        with market_session() as session:
            r = session.post(
                "https://emappdata.eastmoney.com/stockrank/getHotStockRankList",
                json={"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38", "srcSecurityCode": prefix + code},
                headers={"User-Agent": UA}, timeout=10)
        data = r.json().get("data") or []
    except Exception:
        return []
    return [{"concept": x.get("conceptName"), "bk": x.get("conceptId"), "hit": x.get("hitCount")} for x in data]


def investor_qa(code: str, page_size: int = 30) -> list[dict]:
    """互动易问答（巨潮）：投资者提问 + 公司回复（answer=None 表示未回复）。"""
    try:
        with market_session() as session:
            r1 = session.post("https://irm.cninfo.com.cn/newircs/index/queryKeyboardInfo",
                              data={"keyWord": code}, headers={"User-Agent": UA}, timeout=10)
            d1 = r1.json().get("data") or []
            if not d1:
                return []
            org_id = d1[0].get("secid")
            params = {"_t": 1, "stockcode": code, "orgId": org_id, "pageSize": page_size,
                      "pageNum": 1, "keyWord": "", "startDay": "", "endDay": ""}
            rows = session.post("https://irm.cninfo.com.cn/newircs/company/question",
                                params=params, headers={"User-Agent": UA}, timeout=10).json().get("rows") or []
    except Exception:
        return []
    out = []
    for it in rows:
        ts = it.get("pubDate")
        out.append({
            "company": it.get("companyShortName"),
            "question": it.get("mainContent"), "answer": it.get("attachedContent"),
            "answerer": it.get("attachedAuthor"),
            "ask_time": datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M") if ts else "",
        })
    return out


def industry_comparison(top_n: int = 20) -> dict:
    """全行业涨跌幅排名（东财行业板块，~100 个行业）：板块级涨跌 / 涨跌家数 / 领涨。"""
    params = {"pn": "1", "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2",
              "fid": "f3",  # fid=f3 + po=1：按涨跌幅降序，否则 top/bottom 切片非涨幅序（a-stock-data §3.7）
              "fs": "m:90+t:2", "fields": "f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141,f207"}
    try:
        d = em_get("https://push2.eastmoney.com/api/qt/clist/get",
                   params=params, headers={"User-Agent": UA}, timeout=15).json()
    except Exception:
        return {"top": [], "bottom": [], "total": 0}
    items = d.get("data", {}).get("diff", [])
    if isinstance(items, dict):
        items = list(items.values())
    if not items:
        return {"top": [], "bottom": [], "total": 0}
    rows = [{
        "rank": i + 1, "name": it.get("f14", ""), "change_pct": it.get("f3", 0),
        "code": it.get("f12", ""), "up_count": it.get("f104", 0), "down_count": it.get("f105", 0),
    } for i, it in enumerate(items)]
    return {"top": rows[:top_n], "bottom": rows[-top_n:], "total": len(rows)}
