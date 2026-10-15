"""Pure next-session calculations. Prediction, observations and verdicts stay separate."""

from __future__ import annotations

import math
from statistics import median

from . import followup_store as store

COUNT_METRICS = {"limit_up_count", "highest_board", "deep_loss_count", "market_limit_down"}


def number(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def quote_valid(q: dict, eval_date: str, now, final: bool) -> bool:
    if final and q.get("settled_date") == eval_date:
        settled = eval_date < now.date().isoformat() or (eval_date == now.date().isoformat() and now.strftime("%H:%M") >= "15:00")
        return settled and number(q.get("pct")) is not None
    ts = store.parse_time(q.get("quote_time"))
    if ts is None or ts.date().isoformat() != eval_date or number(q.get("pct")) is None:
        return False
    if final:
        return ts.strftime("%H:%M") >= "15:00" and ts <= now
    return 0 <= (now - ts).total_seconds() <= 180


def summarize_members(members, quotes, pools, eval_date, now, final):
    zt, dt = pools.get("zt"), pools.get("dt")
    up_codes = {s["code"] for s in zt} if zt is not None else None
    down_codes = {s["code"] for s in dt} if dt is not None else None
    rows, vals = [], []
    for member in members:
        q = quotes.get(member["code"], {})
        valid = quote_valid(q, eval_date, now, final)
        pct = q.get("pct") if valid else None
        if pct is not None:
            vals.append(pct)
        rows.append({**member, "pct": pct, "price": q.get("price") if valid else None,
                     "quote_time": q.get("quote_time"),
                     "limit_up": member["code"] in up_codes if up_codes is not None else None,
                     "limit_down": member["code"] in down_codes if down_codes is not None else None,
                     "reason": "" if valid else "缺失或过期行情"})
    n = len(rows)
    return {"members": rows, "sample": n, "valid": len(vals),
            "coverage": len(vals) / n if n else None,
            "median": median(vals) if vals else None,
            "up_count": sum(v > 0 for v in vals) if vals else None,
            "deep_loss_count": sum(v <= -5 for v in vals) if vals else None,
            "limit_up_count": sum(r["limit_up"] is True for r in rows) if up_codes is not None else None,
            "limit_down_count": sum(r["limit_down"] is True for r in rows) if down_codes is not None else None}


def metric_readings(prediction, observation, *, final=False):
    now = store.parse_time(observation["observed_at"])
    ev, pools = observation["eval_date"], observation["pools"]
    zt, zb, dt = (pools.get(k) for k in ("zt", "zb", "dt"))
    quotes = observation.get("quotes", {})
    universe = prediction["universe"]
    cohort = summarize_members(universe["stocks"], quotes, pools, ev, now, final)
    firsts = [s for s in universe["stocks"] if s.get("boards") == 1]
    up = {s["code"] for s in zt} if zt is not None else set()
    attempted = len(zt) + len(zb) if zt is not None and zb is not None else 0
    values = {
        "limit_up_count": len(zt) if zt is not None else None,
        "highest_board": max((s.get("boards") or 1 for s in zt), default=0) if zt is not None else None,
        "promotion_1to2": sum(s["code"] in up for s in firsts) / len(firsts) if firsts and zt is not None else None,
        "money_effect_median": cohort["median"],
        "broken_rate": len(zb) / attempted if attempted else None,
        "deep_loss_count": cohort["deep_loss_count"],
        "market_limit_down": len(dt) if dt is not None else None,
        "theme_concentration": observation.get("theme_concentration") if final else None,
    }
    readings = {k: {"value": v, "ready": v is not None, "sample": None, "valid": None,
                    "reason": "" if v is not None else "数据不足"} for k, v in values.items()}
    for k in ("money_effect_median", "deep_loss_count"):
        enough = cohort["valid"] == cohort["sample"] if final else (cohort["coverage"] or 0) >= .9
        readings[k].update(sample=cohort["sample"], valid=cohort["valid"],
                           ready=bool(universe["complete"] and cohort["sample"] and enough),
                           reason="" if universe["complete"] and enough else "原名单不完整或有效行情覆盖不足")
    readings["promotion_1to2"].update(sample=len(firsts), valid=len(firsts) if zt is not None else 0,
                                      ready=bool(universe["complete"] and firsts and zt is not None))
    if not final:
        readings["theme_concentration"]["reason"] = "行业集中度：盘中暂不支持，收盘沿用原口径"
    return readings, cohort


def evaluate(prediction: dict, observation: dict, *, final=False, pending=False) -> dict:
    readings, cohort = metric_readings(prediction, observation, final=final)
    eligible = store.parse_time(prediction["recorded_at"]) < store.parse_time(store.cutoff(observation["eval_date"]))
    results = []
    for item in prediction["items"]:
        rd = readings.get(item["metric"], {"value": None, "ready": False, "reason": "未支持指标"})
        base, current, eps = number(item.get("base_value")), number(rd["value"]), number(item.get("eps"))
        delta = current - base if current is not None and base is not None else None
        actual = None if delta is None or eps is None else (
            "持平" if abs(delta) <= eps + 1e-9 else "上升" if delta > 0 else "下降")
        ready = rd["ready"] and actual is not None and item.get("base_complete", True)
        verified, state, reason = None, "数据不足", rd.get("reason", "")
        if not item.get("base_complete", True):
            reason = "N 日基准样本不完整，不能正式核验"
        if ready:
            if final:
                if pending:
                    state = "待定稿"
                elif eligible:
                    verified = actual == item["direction"]
                    state = "成立" if verified else "不成立"
                else:
                    state, reason = "仅供对照", "缺少盘前记录的预测版本，不计入正式命中率"
            elif item["metric"] in COUNT_METRICS:
                state = "尚待收盘"
            elif actual == item["direction"]:
                state = "暂时支持"
            elif actual == "持平":
                state = "尚待观察"
            else:
                state = "暂时相反"
        results.append({**item, "current_value": current, "delta": delta, "actual": actual,
                        "state": state, "verified": verified, "reason_detail": reason,
                        "ready": bool(ready), "sample": rd.get("sample"), "valid": rd.get("valid")})
    directions = []
    for direction in prediction["directions"]:
        stats = summarize_members(direction["members"], observation.get("quotes", {}), observation["pools"],
                                  observation["eval_date"], store.parse_time(observation["observed_at"]), final)
        explicit = summarize_members(direction["explicit_members"], observation.get("quotes", {}), observation["pools"],
                                     observation["eval_date"], store.parse_time(observation["observed_at"]), final)
        directions.append({**direction, "stats": stats, "explicit": explicit})
    decided = [r for r in results if r["verified"] is not None]
    hits = sum(r["verified"] for r in decided)
    member_data_complete = all(
        stats["sample"] == 0 or (stats["valid"] == stats["sample"]
                                 and stats["limit_up_count"] is not None and stats["limit_down_count"] is not None)
        for d in directions for stats in (d["stats"], d["explicit"]))
    return {"schema": 1, "prediction_date": prediction["prediction_date"],
            "prediction_version": prediction["version"], "eval_date": observation["eval_date"],
            "observed_at": observation["observed_at"], "sources": observation.get("sources", {}),
            "stage": "closing" if final and pending else "final" if final else "intraday", "eligible": eligible,
            "items": results, "directions": directions, "cohort": cohort,
            "summary": {"total": len(results), "decided": len(decided), "hit": hits,
                        "undecidable": len(results) - len(decided),
                        "rate": hits / len(decided) if decided else None},
            "complete": all(r["ready"] for r in results) and member_data_complete}
