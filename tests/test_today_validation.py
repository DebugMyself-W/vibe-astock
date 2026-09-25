from datetime import datetime

from duanxian import today_validation as tv
from duanxian import followup_data
from duanxian.validation_domain import SnapshotType, TradingSnapshot, ValidationStatus


def _item(state, value):
    return {
        "metric": "broken_rate", "label": "炸板率", "direction": "下降",
        "reason": "分歧应当收敛", "base_value": .4, "eps": .05,
        "current_value": value, "actual": None if value is None else "下降" if value < .35 else "上升",
        "state": state, "unit": "",
    }


def _snapshot(at, state, value, stage="intraday"):
    return {"observed_at": at, "stage": stage, "items": [_item(state, value)],
            "directions": [], "cohort": {"members": []}, "sources": {}}


def test_domain_snapshot_rejects_invalid_coverage():
    try:
        TradingSnapshot(
            trade_date="2026-09-18", type=SnapshotType.previous_close,
            captured_at=datetime.fromisoformat("2026-09-18T15:00:00+08:00"), coverage=1.1,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("coverage above one must be rejected")


def test_validation_history_is_append_only_and_events_are_deduplicated():
    history = [
        _snapshot("2026-09-19T10:00:00+08:00", "尚待观察", .38),
        _snapshot("2026-09-19T10:30:00+08:00", "尚待观察", .37),
        _snapshot("2026-09-19T11:30:00+08:00", "暂时支持", .30),
    ]
    prediction = {"version": "v1", "items": [_item("", None)]}
    items = tv._validation_items(prediction, history[-1], history)
    assert [x["status"] for x in items[0]["history"]] == [
        ValidationStatus.watching.value, ValidationStatus.satisfied.value,
    ]
    events = tv._events(history)
    assert len(events) == 1
    assert events[0]["before"] == .37
    assert events[0]["after"] == .30


def test_checkpoints_never_fill_future_data():
    now = datetime.fromisoformat("2026-09-19T10:12:00+08:00")
    rows = tv._checkpoint_rows(
        [_snapshot("2026-09-19T10:00:00+08:00", "暂时支持", .3)],
        "2026-09-19", now,
    )
    assert rows[0]["state"] == "missing"  # auction was due but never captured
    assert rows[1]["state"] == "completed"
    assert all(row["state"] == "upcoming" for row in rows[2:])


def test_build_uses_only_persisted_selected_snapshot(monkeypatch):
    review = {"focus": {"emotion_phase": "修复", "risk_alerts": ["高位分歧"]},
              "emotion_regime": {"phase": "修复", "phase_label": "修复观察", "data_coverage": .8}}
    prediction = {"version": "v1", "items": [_item("", None)], "directions": []}
    ten = _snapshot("2026-09-19T10:00:00+08:00", "尚待观察", .38)
    later = _snapshot("2026-09-19T14:30:00+08:00", "暂时支持", .30)
    monkeypatch.setattr(tv.review_store, "load", lambda _: review)
    monkeypatch.setattr(tv.review_store, "dates", lambda: ["2026-09-18"])
    monkeypatch.setattr(tv.followup_store, "get", lambda date, at: {
        "prediction": prediction, "snapshot": ten, "final": None, "eval_date": "2026-09-19",
        "status": {"phase": "盘中", "delayed": False}, "times": ["20260919T1000", "20260919T1430"],
    })
    monkeypatch.setattr(tv, "_read_history", lambda *_: [ten, later])
    result = tv.build("2026-09-18", "20260919T1000", datetime.fromisoformat("2026-09-19T15:00:00+08:00"))
    assert result["validations"][0]["current_value"] == .38
    assert result["observed_at"] == ten["observed_at"]


def test_live_quote_recovers_calendar_when_history_provider_is_empty(monkeypatch):
    """A confirmed live session must not stay at 'trading day pending'."""
    now = datetime.fromisoformat("2026-09-21T09:38:00+08:00")
    monkeypatch.setattr(followup_data.trade_calendar, "_ref_dates", lambda *_: [])
    monkeypatch.setattr(followup_data.trade_calendar, "quote_trade_day", lambda: "2026-09-21")
    monkeypatch.setattr(followup_data.trade_calendar, "prev_trade_date", lambda _: "2026-09-18")
    monkeypatch.setattr(followup_data.time, "monotonic", lambda: 100.0)
    followup_data._calendar_cache = None
    assert followup_data.calendar_days(now) == ["2026-09-18", "2026-09-21"]


def test_feedback_does_not_turn_missing_down_pool_into_zero():
    feedback = tv._feedback({
        "sources": {"zt": {"available": True}, "dt": {"available": False, "reason": "上游超时"}},
        "cohort": {"members": [
            {"code": "000001", "pct": -9.9, "limit_up": False, "limit_down": None},
            {"code": "000002", "pct": 1.2, "limit_up": False, "limit_down": None},
        ]},
    })
    buckets = {row["label"]: row for row in feedback["buckets"]}
    assert feedback["complete"] is False
    assert "dt" in feedback["missing_sources"]
    assert buckets["跌停"]["count"] is None
    assert buckets["跌超5%"]["count"] is None


def test_regime_dimensions_are_comparable_scores_not_mixed_raw_units():
    review = {"emotion_regime": {"data_coverage": 1, "retreat": {"signals": [
        {"key": "feedback", "label": "赚钱反馈", "state": "healthy", "value": 1.23, "detail": "中位 +1.23%"},
        {"key": "ladder", "label": "涨停梯队", "state": "risk", "value": 78, "detail": "涨停 78 家"},
    ]}}}
    regime = tv._regime(review)
    assert all(0 <= value <= 10 for value in regime["dimensions"].values())
    assert regime["dimension_evidence"]["涨停梯队"]["raw_value"] == 78
    assert regime["dimensions"]["赚钱反馈"] != 1.23


def test_unbound_theme_is_not_presented_as_neutral_market_data():
    themes = tv._themes({
        "directions": [{"id": "0", "direction": "次新股", "logic": "情绪扩散", "risk": "退潮"}],
        "universe": {"stocks": []},
    }, {"directions": [{"id": "0", "stats": {}}], "cohort": {"members": []}})

    assert themes[0]["lifecycle"] == "数据不足"
    assert themes[0]["data_status"] == "missing"
    assert themes[0]["complete"] is False
    assert "冻结" in themes[0]["missing_reason"]


def test_unbound_combined_theme_can_use_only_exact_frozen_sector_as_partial_sample():
    prediction = {
        "directions": [{"id": "0", "direction": "AI算力与半导体", "logic": "科技主线", "risk": "分歧"}],
        "universe": {"stocks": [
            {"code": "600001", "name": "芯片甲", "sector": "半导体", "boards": 1},
            {"code": "600002", "name": "通信乙", "sector": "通信设备", "boards": 1},
        ]},
    }
    selected = {"directions": [{"id": "0", "stats": {}}], "cohort": {"members": [
        {"code": "600001", "name": "芯片甲", "sector": "半导体", "pct": 4.0},
        {"code": "600002", "name": "通信乙", "sector": "通信设备", "pct": 1.0},
    ]}}

    theme = tv._themes(prediction, selected)[0]

    assert theme["data_status"] == "partial"
    assert theme["complete"] is False
    assert theme["strength"] == 4.0
    assert theme["coverage"] == 1.0
    assert theme["sample_size"] == 1
    assert theme["binding_basis"] == ["半导体"]
    assert "AI算力" in theme["missing_reason"]
