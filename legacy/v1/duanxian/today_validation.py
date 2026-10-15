"""Read-only V1 projection for the Today Validation page.

The source of truth remains review_store + followup_store.  This module only
maps those versioned artifacts into the V0 vocabulary and never recomputes a
historical point from current quotes.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

from . import followup_store, review_store
from .validation_domain import SnapshotType, ValidationStatus


CHECKPOINTS = [
    (SnapshotType.auction_0925, "09:25", "竞价"),
    (SnapshotType.morning_1000, "10:00", "上午"),
    (SnapshotType.midday_1130, "11:30", "午间"),
    (SnapshotType.late_1430, "14:30", "尾盘"),
    (SnapshotType.close_1500, "15:00", "收盘"),
]

_STATE_MAP = {
    "成立": ValidationStatus.satisfied,
    "暂时支持": ValidationStatus.satisfied,
    "不成立": ValidationStatus.invalidated,
    "暂时相反": ValidationStatus.invalidated,
    "尚待观察": ValidationStatus.watching,
    "尚待收盘": ValidationStatus.watching,
    "待定稿": ValidationStatus.watching,
    "数据不足": ValidationStatus.insufficient_data,
    "仅供对照": ValidationStatus.expired,
}


def _dt(value: str | None) -> datetime | None:
    return followup_store.parse_time(value) if value else None


def _kind(observed_at: str | None, stage: str = "") -> SnapshotType:
    if stage in {"final", "closing"}:
        return SnapshotType.close_1500
    value = _dt(observed_at)
    hhmm = value.strftime("%H:%M") if value else ""
    if hhmm < "09:30":
        return SnapshotType.auction_0925
    if hhmm < "11:30":
        return SnapshotType.morning_1000
    if hhmm < "14:30":
        return SnapshotType.midday_1130
    return SnapshotType.late_1430


def _status(item: dict, has_snapshot: bool) -> str:
    if not has_snapshot:
        return ValidationStatus.pending.value
    return _STATE_MAP.get(str(item.get("state") or ""), ValidationStatus.insufficient_data).value


def _read_history(prediction_date: str, final: dict | None) -> list[dict]:
    root = followup_store.folder(prediction_date)
    rows: list[dict] = []
    for path in sorted((root / "snapshots").glob("*.json")):
        payload = followup_store.read_json(path)
        if payload:
            rows.append(payload)
    if final:
        rows.append(final)
    return rows


def _checkpoint_rows(history: list[dict], eval_date: str | None, now: datetime) -> list[dict]:
    result = []
    for kind, time, label in CHECKPOINTS:
        if kind == SnapshotType.close_1500:
            candidates = [r for r in history if r.get("stage") in {"final", "closing"}]
            stored = candidates[-1] if candidates else None
        else:
            target = _dt(f"{eval_date}T{time}:00+08:00") if eval_date else None
            candidates = [(abs((_dt(r.get("observed_at")) - target).total_seconds()), r)
                          for r in history if target and _dt(r.get("observed_at"))
                          and r.get("stage") not in {"final", "closing"}]
            close = [pair for pair in candidates if pair[0] <= 10 * 60]
            stored = min(close, key=lambda pair: pair[0])[1] if close else None
        due = bool(eval_date and (eval_date < now.date().isoformat() or
                   (eval_date == now.date().isoformat() and now.strftime("%H:%M") >= time)))
        state = "completed" if stored else "missing" if due else "upcoming"
        result.append({"key": kind.value, "time": time, "label": label, "state": state,
                       "observed_at": stored.get("observed_at") if stored else None,
                       "history_key": followup_store.history_key(_dt(stored.get("observed_at")))
                       if stored and _dt(stored.get("observed_at")) and kind != SnapshotType.close_1500 else
                       "final" if stored and kind == SnapshotType.close_1500 else None})
    return result


def _validation_items(prediction: dict, selected: dict | None, history: list[dict]) -> list[dict]:
    source = (selected or {}).get("items") or prediction.get("items") or []
    transitions: dict[str, list[dict]] = {}
    for row in history:
        for item in row.get("items") or []:
            key = str(item.get("metric") or "")
            state = _status(item, True)
            trail = transitions.setdefault(key, [])
            if not trail or trail[-1]["status"] != state:
                trail.append({"status": state, "at": row.get("observed_at"),
                              "snapshot": _kind(row.get("observed_at"), row.get("stage", "")).value,
                              "value": item.get("current_value"), "evidence": item.get("reason_detail") or ""})
    out = []
    for index, item in enumerate(source):
        key = str(item.get("metric") or index)
        out.append({
            "id": f"{prediction.get('version', 'legacy')}:{key}",
            "metric": key,
            "label": item.get("label") or key,
            "judgment": item.get("reason") or "等待验证",
            "expected_direction": item.get("direction") or item.get("expect"),
            "baseline": item.get("base_value"),
            "threshold": item.get("eps"),
            "unit": item.get("unit") or "",
            "current_value": item.get("current_value"),
            "actual_direction": item.get("actual"),
            "status": _status(item, selected is not None),
            "status_label": item.get("state") or ("待验证" if selected is None else "数据不足"),
            "evidence": item.get("reason_detail") or "",
            "updated_at": (selected or {}).get("observed_at"),
            "history": transitions.get(key, []),
        })
    return out


def _events(history: list[dict]) -> list[dict]:
    events: list[dict] = []
    previous: dict[str, dict] = {}
    for row in history:
        at = row.get("observed_at")
        for item in row.get("items") or []:
            key = str(item.get("metric") or "")
            state = _status(item, True)
            old = previous.get(key)
            if old and old["status"] != state:
                severity = "risk" if state == ValidationStatus.invalidated.value else "watch"
                raw = f"{key}:{at}:{old['status']}:{state}"
                events.append({"id": hashlib.sha1(raw.encode()).hexdigest()[:12],
                               "kind": "validation_changed", "title": f"{item.get('label', key)}状态变化",
                               "occurred_at": at, "severity": severity,
                               "before": old.get("value"), "after": item.get("current_value"),
                               "threshold": item.get("eps"), "scope": [key],
                               "evidence": f"{old['label']} → {item.get('state') or state}"})
            previous[key] = {"status": state, "label": item.get("state") or state,
                             "value": item.get("current_value")}
    return list(reversed(events[-10:]))


def _sector_fallback(direction: dict, prediction: dict, selected: dict | None) -> dict | None:
    """Build a partial, frozen N-day sample from exact sector-name components.

    This is deliberately narrower than semantic theme guessing: for a combined
    label such as ``AI算力与半导体`` only the exact ``半导体`` sector is eligible.
    The unmatched component remains explicit so the partial sample cannot be
    mistaken for validation of the whole narrative direction.
    """
    name = str(direction.get("direction") or "")
    parts = [part.strip() for part in re.split(r"[／/、，,+＋|｜与]", name) if part.strip()]
    frozen = (prediction.get("universe") or {}).get("stocks") or []
    sectors = {str(row.get("sector") or "") for row in frozen if row.get("sector")}
    matched = [part for part in parts if part in sectors]
    if not matched:
        return None
    codes = {str(row.get("code") or "") for row in frozen if row.get("sector") in matched}
    live_by_code = {str(row.get("code") or ""): row
                    for row in ((selected or {}).get("cohort") or {}).get("members") or []}
    members = [{**row, **live_by_code.get(str(row.get("code") or ""), {})}
               for row in frozen if str(row.get("code") or "") in codes]
    values = [row.get("pct") for row in members if isinstance(row.get("pct"), (int, float))]
    missing = [part for part in parts if part not in matched]
    return {"members": members, "sample": len(members), "valid": len(values),
            "coverage": len(values) / len(members) if members else None,
            "median": median(values) if values else None,
            "basis": matched, "missing": missing}


def _themes(prediction: dict, selected: dict | None) -> list[dict]:
    selected_by_id = {str(d.get("id")): d for d in (selected or {}).get("directions") or []}
    rows = []
    for direction in prediction.get("directions") or []:
        live = selected_by_id.get(str(direction.get("id")), direction)
        stats = live.get("stats") or {}
        members = stats.get("members") or direction.get("members") or []
        complete = bool(members) and live.get("complete") is True
        status = "complete" if complete else "partial" if members else "missing"
        basis = live.get("tags") or direction.get("tags") or []
        missing_reason = "" if complete else str(live.get("binding_reason") or direction.get("binding_reason") or "")

        if not members:
            fallback = _sector_fallback(direction, prediction, selected)
            if fallback:
                stats, members = fallback, fallback["members"]
                status, basis = "partial", fallback["basis"]
                uncovered = "、".join(fallback["missing"])
                missing_reason = f"仅覆盖冻结行业样本：{'、'.join(basis)}"
                if uncovered:
                    missing_reason += f"；未覆盖：{uncovered}"
                missing_reason += "。当前读数不代表整个聚合方向。"
            else:
                missing_reason = "昨晚方向未绑定冻结题材或成员，无法计算盘中强度；需在下次 09:15 锁定前确认。"
        median = stats.get("median")
        coverage = stats.get("coverage")
        measurable = isinstance(median, (int, float)) and isinstance(coverage, (int, float))
        rows.append({"id": str(direction.get("id")), "name": direction.get("direction") or "未命名题材",
                     "lifecycle": ("扩散" if median > 1 else "退潮" if median < -2 else "震荡")
                                  if measurable else "数据不足",
                     "strength": median if measurable else None, "coverage": coverage,
                     "members": members, "sample_size": len(members),
                     "complete": complete, "data_status": status,
                     "binding_basis": basis, "missing_reason": missing_reason,
                     "logic": direction.get("logic") or "", "risk": direction.get("risk") or ""})
    return rows


def _feedback(selected: dict | None) -> dict:
    cohort = (selected or {}).get("cohort") or {}
    members = cohort.get("members") or []
    sources = (selected or {}).get("sources") or {}
    zt_source, dt_source = sources.get("zt") or {}, sources.get("dt") or {}
    up_available = zt_source.get("available") is True or bool(members) and all(
        row.get("limit_up") is not None for row in members)
    down_available = dt_source.get("available") is True or bool(members) and all(
        row.get("limit_down") is not None for row in members)
    buckets = {"晋级涨停": 0, "收红": 0, "小跌": 0, "跌超5%": 0, "跌停": 0}
    unclassified = 0
    for row in members:
        pct = row.get("pct")
        if up_available and row.get("limit_up"):
            buckets["晋级涨停"] += 1
        elif down_available and row.get("limit_down"):
            buckets["跌停"] += 1
        elif isinstance(pct, (int, float)) and pct > 0 and up_available:
            buckets["收红"] += 1
        elif isinstance(pct, (int, float)) and pct <= -5 and down_available:
            buckets["跌超5%"] += 1
        elif isinstance(pct, (int, float)) and -5 < pct <= 0:
            buckets["小跌"] += 1
        else:
            unclassified += 1
    total = sum(buckets.values())
    if not up_available:
        buckets["晋级涨停"] = buckets["收红"] = None
    if not down_available:
        buckets["跌超5%"] = buckets["跌停"] = None
    missing = (["zt"] if not up_available else []) + (["dt"] if not down_available else [])
    return {"total": len(members), "classified": total, "unclassified": unclassified,
            "complete": not missing, "missing_sources": missing,
            "reason": "、".join(f"{key}池不可用" for key in missing),
            "buckets": [{"label": k, "count": v,
                         "ratio": v / len(members) if v is not None and members else None}
                        for k, v in buckets.items()], "members": members}


def _regime(review: dict) -> dict:
    raw = review.get("emotion_regime") or {}
    dims = {}
    evidence = {}
    for signal in ((raw.get("retreat") or {}).get("signals") or []):
        key = str(signal.get("key") or "")
        label = signal.get("label") or key
        value = signal.get("value")
        score = None
        if isinstance(value, (int, float)):
            score = {
                "feedback": lambda v: 5 + v,
                "continuation": lambda v: v * 20,
                "seal": lambda v: (1 - v) * 10,
                "loss": lambda v: v * 100,
                "ladder": lambda v: v / 10,
                "breadth": lambda v: v * 10,
            }.get(key, lambda _v: 7 if signal.get("state") == "healthy" else 3)(value)
            score = round(max(0, min(10, score)), 1)
        dims[label] = score
        evidence[label] = {"key": key, "score": score, "raw_value": value,
                           "state": signal.get("state"), "detail": signal.get("detail") or ""}
    return {"phase": raw.get("phase") or (review.get("focus") or {}).get("emotion_phase"),
            "phase_label": raw.get("phase_label") or "数据不足", "confidence": raw.get("confidence") or "low",
            "coverage": raw.get("data_coverage") or 0, "summary": raw.get("summary") or "暂无情绪定稿",
            "dimensions": dims, "dimension_evidence": evidence, "issues": raw.get("issues") or []}


def build(prediction_date: str | None = None, at: str | None = None, now: datetime | None = None) -> dict:
    now = now or followup_store.china_now()
    if not prediction_date:
        dates = review_store.dates()
        prediction_date = dates[0] if dates else None
    if not prediction_date:
        return {"available": False, "reason": "尚无收盘复盘，无法建立今日验证条件"}
    review = review_store.load(prediction_date)
    followup = followup_store.get(prediction_date, at)
    prediction = followup.get("prediction") or {}
    if not review or not prediction:
        return {"available": False, "prediction_date": prediction_date,
                "reason": "该日复盘尚未生成可验证条件"}
    selected = followup.get("snapshot")
    history = _read_history(prediction_date, followup.get("final"))
    # A historical read must not expose later transitions, events or checkpoints.
    if at and selected and _dt(selected.get("observed_at")):
        selected_at = _dt(selected.get("observed_at"))
        history = [row for row in history if _dt(row.get("observed_at")) and
                   _dt(row.get("observed_at")) <= selected_at]
    items = _validation_items(prediction, selected, history)
    counts = {s.value: sum(i["status"] == s.value for i in items) for s in ValidationStatus}
    return {
        "available": True, "schema_version": 1, "prediction_date": prediction_date,
        "eval_date": followup.get("eval_date"), "observed_at": (selected or {}).get("observed_at"),
        "stage": (followup.get("status") or {}).get("phase") or "等待开盘",
        "editable": followup.get("editable", False), "eligible": followup.get("eligible", False),
        "delayed": (followup.get("status") or {}).get("delayed", False),
        "checkpoints": _checkpoint_rows(history, followup.get("eval_date"), now),
        "summary": {"total": len(items), **counts}, "validations": items,
        "regime": _regime(review), "themes": _themes(prediction, selected),
        "feedback": _feedback(selected), "events": _events(history),
        "risks": (review.get("focus") or {}).get("risk_alerts") or [],
        "sources": (selected or {}).get("sources") or {},
        "available_times": followup.get("times") or [],
    }
