"""全市场涨跌宽度 —— 回答「今天到底好不好做」"""

from __future__ import annotations

from duanxian.paths import data_path
import json
import logging
import math
import os
import time
import urllib.parse
import urllib.request
from typing import Optional

from net_utils import market_urlopen

from . import trade_calendar
from .util import atomic_write_json

logger = logging.getLogger(__name__)

_CACHE_DIR = data_path("cache/breadth")
_SCHEMA = 1

_UA = {"User-Agent": "Mozilla/5.0"}
_HOST = "https://push2delay.eastmoney.com"     # 本机 push2 被封，走延时源（复盘够用）
# 全 A：沪深主板 + 创业板 + 科创板 + 北交所
_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
_PZ = 100
_SLEEP = 0.35                                  # 二分探测之间的节流，别把源打毛
_PROBE_SPAN = 3                                # 探到空页时向两侧最多找几页
_TIMEOUT = 25

_INDEX_IDS = ("1.000001", "0.399001")

_SINA_LIST = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
_SINA_COUNT = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount"
_SINA_PZ = 100


def _get(url: str, retries: int = 2) -> dict:
    """带一次重试"""
    if retries < 1:
        raise ValueError(f"retries 必须 >= 1，得到 {retries}")   # 别伪装成"请求失败"
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with market_urlopen(req, timeout=_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i + 1 < retries:
                time.sleep(0.8)
    raise last if last else RuntimeError("请求失败")


def _index_breadth() -> Optional[dict]:
    """沪深两市涨跌平家数 + 成交额。一个请求。"""
    url = (f"{_HOST}/api/qt/ulist.np/get?fltt=2&secids={','.join(_INDEX_IDS)}"
           f"&fields=f1,f2,f3,f6,f104,f105,f106")
    try:
        diff = ((_get(url).get("data") or {}).get("diff")) or []
        if len(diff) < len(_INDEX_IDS):
            return None

        def _num(row, key):
            v = row.get(key)
            # math.isfinite 挡掉 NaN / inf —— 它们都是 float，isinstance 拦不住
            return float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None

        up = down = flat = 0
        amount = 0.0
        for row in diff:
            if not isinstance(row, dict):
                return None
            u, d, f, a = (_num(row, "f104"), _num(row, "f105"),
                          _num(row, "f106"), _num(row, "f6"))
            if None in (u, d, f, a) or min(u, d, f, a) < 0:
                return None
            up, down, flat, amount = up + int(u), down + int(d), flat + int(f), amount + a
        return {"up": up, "down": down, "flat": flat, "amount_yi": round(amount / 1e8, 1)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("取指数宽度失败：%s: %s", type(exc).__name__, exc)
        return None


def _page(pn: int) -> tuple[int, list[float]]:
    """按涨跌幅**升序**的第 pn 页。返回 `(全市场只数, 可用涨跌幅列表, 原始行数)`"""
    url = (f"{_HOST}/api/qt/clist/get?pn={pn}&pz={_PZ}&po=0&np=1&fltt=2&invt=2"
           f"&fid=f3&fs={_FS}&fields=f3")
    d = _get(url).get("data")
    if not isinstance(d, dict):
        raise ValueError(f"第 {pn} 页响应没有 data")
    diff = d.get("diff")
    if not isinstance(diff, list):
        raise ValueError(f"第 {pn} 页响应没有 diff")
    total = d.get("total")
    if not isinstance(total, int) or total <= 0:
        raise ValueError(f"第 {pn} 页 total 不合法：{total!r}")
    vals = [float(x["f3"]) for x in diff
            if isinstance(x, dict) and isinstance(x.get("f3"), (int, float))]
    return total, vals, len(diff)


def _sina_json(url: str, params: dict) -> object:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={"User-Agent": _UA["User-Agent"], "Referer": "https://finance.sina.com.cn/"},
    )
    with market_urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _sina_page(page: int, descending: bool) -> tuple[list[float], int]:
    payload = _sina_json(_SINA_LIST, {
        "page": page, "num": _SINA_PZ, "sort": "changepercent",
        "asc": 0 if descending else 1, "node": "hs_a", "symbol": "", "_s_r_a": "page",
    })
    if not isinstance(payload, list):
        raise ValueError(f"新浪第 {page} 页响应不是列表")
    values = []
    for row in payload:
        if not isinstance(row, dict) or row.get("changepercent") in (None, ""):
            continue
        try:
            value = float(row["changepercent"])
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)
    return values, len(payload)


def _sina_prefix_count(total: int, predicate, *, descending: bool, calls: list[int],
                       cache: dict[int, tuple[list[float], int]] | None = None) -> int:
    """Count the true prefix in a reliably sorted Sina market list."""
    max_page = max(1, (total + _SINA_PZ - 1) // _SINA_PZ)
    lo, hi = 1, max_page + 1
    page_cache = cache if cache is not None else {}

    def page(number: int) -> tuple[list[float], int]:
        if number not in page_cache:
            page_cache[number] = _sina_page(number, descending)
            calls[0] += 1
        values, raw = page_cache[number]
        expected = _SINA_PZ if number < max_page else total - (max_page - 1) * _SINA_PZ
        if raw != expected or len(values) != raw:
            raise ValueError(f"新浪第 {number} 页行数不完整：{raw}/{expected}")
        return values, raw

    while lo < hi:
        mid = (lo + hi) // 2
        values, _ = page(mid)
        if values and predicate(values[-1]):
            lo = mid + 1
        else:
            hi = mid
    if lo == max_page + 1:
        return total
    values, _ = page(lo)
    return (lo - 1) * _SINA_PZ + sum(1 for value in values if predicate(value))


def _sina_distribution() -> dict:
    """Independent Shanghai/Shenzhen breadth fallback from sorted Sina pages."""
    calls = [0]
    raw_total = _sina_json(_SINA_COUNT, {"node": "hs_a"})
    calls[0] += 1
    try:
        total = int(raw_total)
    except (TypeError, ValueError):
        raise ValueError(f"新浪全市场只数不合法：{raw_total!r}") from None
    if total <= 0:
        raise ValueError(f"新浪全市场只数不合法：{total}")
    ascending: dict[int, tuple[list[float], int]] = {}
    descending: dict[int, tuple[list[float], int]] = {}
    down5 = _sina_prefix_count(
        total, lambda value: value < -5.0, descending=False, calls=calls, cache=ascending,
    )
    down = _sina_prefix_count(
        total, lambda value: value < 0, descending=False, calls=calls, cache=ascending,
    )
    nonpositive = _sina_prefix_count(
        total, lambda value: value <= 0, descending=False, calls=calls, cache=ascending,
    )
    up5 = _sina_prefix_count(
        total, lambda value: value >= 5.0, descending=True, calls=calls, cache=descending,
    )
    return {
        "universe": total,
        "up": total - nonpositive,
        "down": down,
        "flat": nonpositive - down,
        "deep_up_5_incl": up5,
        "deep_down_5": down5,
        "source": "sina_market_center",
        "requests": calls[0],
    }


def _tencent_index_amount(date: str) -> float:
    """Return the same-date Shanghai + Shenzhen turnover from Tencent indices."""
    from vr.astock import _fetch_gtimg, _parse_gtimg

    quotes = _parse_gtimg(_fetch_gtimg(["sh000001", "sz399001"]))
    expected = date.replace("-", "")
    rows = [quotes.get("000001") or {}, quotes.get("399001") or {}]
    if any(str(row.get("quote_time") or "")[:8] != expected for row in rows):
        actual = sorted({str(row.get("quote_time") or "")[:8] for row in rows})
        raise ValueError(f"腾讯指数行情日不匹配：期望 {expected}，实际 {actual}")
    amounts = [row.get("amount_wan") for row in rows]
    if not all(_num_ok(value) and value > 0 for value in amounts):
        raise ValueError("腾讯指数成交额不完整")
    return round(sum(amounts) / 10000, 1)


def _rank_below(total: int, thr: float, calls: list) -> Optional[int]:
    """升序表里涨跌幅 < thr 的只数。二分定位到页，再在页内数"""
    max_pn = max(1, (total + _PZ - 1) // _PZ)
    lo, hi = 1, max_pn

    def probe(pn: int, lo_: int, hi_: int):
        """从 pn 出发就近找一个**有值**的页。返回 (页号, vals) 或 None。"""
        for delta in range(0, _PROBE_SPAN + 1):
            for cand in ((pn,) if delta == 0 else (pn - delta, pn + delta)):
                if not (lo_ <= cand <= hi_):
                    continue
                t, vals, raw = _page(cand)
                calls[0] += 1
                time.sleep(_SLEEP)
                if cand != max_pn and raw != _PZ:
                    raise ValueError(f"第 {cand} 页只有 {raw} 行（非末页应满 {_PZ} 行）")
                if t != total:
                    raise ValueError(f"全市场只数中途变了：{total} → {t}")
                if vals:
                    return cand, vals
        return None

    try:
        guard = 0
        while lo < hi:
            guard += 1
            if guard > max_pn + 8:       # 防"探到的页不是 mid"导致的收敛停滞
                logger.warning("二分定位 %.1f%% 边界未收敛，放弃", thr)
                return None
            got = probe((lo + hi) // 2, lo, hi)
            if got is None:
                logger.warning("二分定位 %.1f%% 边界：附近全是无数据页，判不出方向", thr)
                return None
            pn, vals = got
            if vals[-1] < thr:
                lo = pn + 1
            else:
                hi = pn
        got = probe(lo, lo, max_pn)
        if got is None:
            return None
        pn, vals = got
        if pn != lo:
            logger.warning("二分定位 %.1f%% 边界：目标页无值，放弃", thr)
            return None
        return (lo - 1) * _PZ + sum(1 for v in vals if v < thr)
    except Exception as exc:  # noqa: BLE001
        logger.warning("二分定位 %.1f%% 边界失败：%s: %s", thr, type(exc).__name__, exc)
        return None


def _cache_path(date: str) -> str:
    return os.path.join(_CACHE_DIR, f"{date}.json")


def _num_ok(v: object) -> bool:
    """是不是一个可用的非负有限数"""
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v) and v >= 0)


def up5_of(d: dict) -> object:
    """读「涨幅≥5% 家数」，**兼容改名前的旧字段**"""
    v = d.get("deep_up_5_incl")
    return v if v is not None else d.get("deep_up_5")


def _payload_ok(d: object) -> bool:
    """缓存里那份 `data` 是不是完整可用"""
    if not isinstance(d, dict) or d.get("available") is not True:
        return False
    for k in ("up_down_scope", "dist_scope"):
        if not isinstance(d.get(k), str) or not d[k]:
            return False
    if not all(_num_ok(d.get(k)) for k in ("up", "down", "flat", "amount_yi")):
        return False
    for k in ("dist_available", "dist_partial"):
        if not isinstance(d.get(k), bool):
            return False
    if d["dist_available"]:
        # 声称有分布 → 必须真的有：只数合法，且至少一项分布值是合法数
        if not _num_ok(d.get("universe")):
            return False
        if not any(_num_ok(v) for v in (up5_of(d), d.get("deep_down_5"))):
            return False
    return True


def market_breadth(date: str) -> dict:
    """`date` 收盘时的全市场宽度。历史日走落盘缓存，零网络。

    返回里 `available=False` 时一定带 `reason` —— **绝不用 0 或空充数**。
    """
    path = _cache_path(date)
    cached_data = None
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                env = json.load(fh)
            if (isinstance(env, dict) and env.get("schema") == _SCHEMA
                    and env.get("date") == date and _payload_ok(env.get("data"))):
                cached_data = env["data"]
                if cached_data.get("dist_available"):
                    return cached_data
        except Exception:  # noqa: BLE001  缓存坏了当没有，重新取
            pass

    ok, why = trade_calendar.live_quotes_are_close_of(date)
    if not ok:
        if cached_data is not None:
            return cached_data
        return {"available": False, "reason": why}

    calls = [0]
    independent = None
    up_down_source = "eastmoney_index_width"
    up_down_scope = "沪深两市（不含北交所）"
    idx = _index_breadth()
    calls[0] += 1
    if idx is None:
        if cached_data is not None:
            idx = {key: cached_data[key] for key in ("up", "down", "flat", "amount_yi")}
            up_down_source = str(cached_data.get("up_down_source") or "settled_cache")
            up_down_scope = str(cached_data.get("up_down_scope") or up_down_scope)
        else:
            try:
                independent = _sina_distribution()
                idx = {key: independent[key] for key in ("up", "down", "flat")}
                idx["amount_yi"] = _tencent_index_amount(date)
                up_down_source = "sina_market_center+tencent_indices"
                up_down_scope = "沪深 A 股（新浪行情中心）"
                calls[0] += int(independent.get("requests") or 0) + 1
            except Exception as exc:  # noqa: BLE001 - both sources must agree on the target close
                logger.warning("涨跌家数独立备用源失败：%s: %s", type(exc).__name__, exc)
                return {
                    "available": False,
                    "reason": "取全市场涨跌家数失败（东财主源与新浪/腾讯备用源均不可用）",
                }

    total = int(independent.get("universe") or 0) if independent else 0
    down5 = independent.get("deep_down_5") if independent else None
    up5 = independent.get("deep_up_5_incl") if independent else None
    dist_source = independent.get("source") if independent else None

    if independent is None:
        try:
            total, _, _ = _page(1)
            calls[0] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("取全市场只数失败，分布这几项本次不给：%s: %s", type(exc).__name__, exc)

        down5 = _rank_below(total, -5.0, calls) if total else None
        up5_below = _rank_below(total, 5.0, calls) if total else None
        up5 = (total - up5_below) if (total and up5_below is not None) else None
        dist_source = "eastmoney_clist" if any(value is not None for value in (up5, down5)) else None

    if up5 is None or down5 is None:
        try:
            fallback = _sina_distribution()
            total = fallback["universe"]
            up5 = fallback["deep_up_5_incl"]
            down5 = fallback["deep_down_5"]
            dist_source = fallback["source"]
            calls[0] += fallback["requests"]
        except Exception as exc:  # noqa: BLE001 - 保留主源已成功的部分，绝不补零
            logger.warning("新浪涨跌分布备用源失败：%s: %s", type(exc).__name__, exc)

    out = {
        "available": True,
        "date": date,
        # 沪深两市（不含北交所）—— 口径如实写出来，别声称"全市场"
        "up": idx["up"], "down": idx["down"], "flat": idx["flat"],
        "up_down_scope": up_down_scope,
        "up_down_source": up_down_source,
        "amount_yi": idx["amount_yi"],
        # 极端涨跌分布的市场范围由 dist_scope 明示；备用源只覆盖沪深 A 股。
        "universe": total,
        "deep_up_5_incl": up5, "deep_down_5": down5,
        "dist_scope": ("沪深 A 股（新浪行情中心）"
                       if dist_source == "sina_market_center" else "全 A（含北交所）"),
        "dist_available": any(v is not None for v in (up5, down5)),
        "dist_partial": any(v is None for v in (up5, down5)),
        "dist_source": dist_source,
        "requests": calls[0],
    }
    if trade_calendar.is_settled(date):
        atomic_write_json(path, {"schema": _SCHEMA, "date": date, "data": out})
    return out


def render(b: dict) -> str:
    """喂 prompt 的文本块。取不到就明说，不编。"""
    if not b.get("available"):
        return f"· 全市场涨跌宽度：不可用（{b.get('reason', '未知')}）"
    up, down, flat = b["up"], b["down"], b["flat"]
    tot = up + down + flat
    ratio = f"{up}涨 / {down}跌 / {flat}平" + (f"（上涨占比 {up / tot:.0%}）" if tot else "")
    line = (f"· 全市场宽度[{b['up_down_scope']}]：{ratio}；"
            f"两市成交额 {b['amount_yi']:.0f} 亿")
    if b.get("dist_available"):
        parts = []
        if up5_of(b) is not None:
            parts.append(f"涨幅≥5% {up5_of(b)} 家")
        if b.get("deep_down_5") is not None:
            parts.append(f"跌超5% {b['deep_down_5']} 家")
        line += f"。涨跌分布[{b['dist_scope']}，共 {b['universe']} 只]：" + "、".join(parts)
        if b.get("dist_partial"):
            line += "（其余几项本次取数失败，未取到 ≠ 为 0）"
    else:
        line += "。涨跌分布取数失败，本次不给（不要据此认为分布正常）"
    return line
