from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vr"))
import limit_alerts  # noqa: E402


@pytest.mark.unit
class TestLimitBreakTracker:
    def test_requires_observed_limit_then_deduplicates_until_reseal(self):
        tracker, code = limit_alerts.LimitBreakTracker(), "600001"
        below = {code: {"name": "测试股", "price": 9.9, "limit_up": 10}}
        sealed = {code: {"name": "测试股", "price": 10, "limit_up": 10}}
        assert tracker.observe([code], below, "2026-08-27", "09:31:00")["events"] == []
        tracker.observe([code], sealed, "2026-08-27", "09:32:00")
        broken = tracker.observe([code], below, "2026-08-27", "09:33:00")
        assert [event["kind"] for event in broken["events"]] == ["break"]
        assert broken["states"][code]["break_count"] == 1
        assert len(tracker.observe([code], below, "2026-08-27", "09:34:00")["events"]) == 1
        tracker.observe([code], sealed, "2026-08-27", "09:35:00")
        second = tracker.observe([code], below, "2026-08-27", "09:36:00")
        assert [event["kind"] for event in second["events"]] == ["break", "reseal", "break"]
        assert second["states"][code]["break_count"] == 2

    def test_new_session_clears_events_and_missing_quote_is_not_a_break(self):
        tracker, code = limit_alerts.LimitBreakTracker(), "600001"
        tracker.observe([code], {code: {"name": "测试股", "price": 10, "limit_up": 10}}, "2026-08-27", "09:30:00")
        tracker.observe([code], {code: {"name": "测试股", "price": 9.9, "limit_up": 10}}, "2026-08-27", "09:31:00")
        next_day = tracker.observe([code], {}, "2026-08-28", "09:30:00")
        assert next_day["events"] == [] and next_day["states"] == {}

    def test_sealed_duration_and_distinct_observations_are_exposed(self):
        tracker, code = limit_alerts.LimitBreakTracker(), "600001"
        sealed = {code: {"name": "测试股", "price": 10, "limit_up": 10}}
        first = tracker.observe([code], sealed, "2026-08-27", "10:00:00")["states"][code]
        second = tracker.observe([code], sealed, "2026-08-27", "10:01:00")["states"][code]
        assert first["sealed_observations"] == 1 and first["sealed_seconds"] == 0
        assert second["sealed_observations"] == 2 and second["sealed_seconds"] == 60

    def test_api_wraps_snapshot_and_rejects_invalid_codes(self, monkeypatch):
        import app as vr_app

        monkeypatch.setattr(vr_app.limit_alerts, "snapshot", lambda codes: {"available": True, "states": {codes[0]: {}}, "events": []})
        assert vr_app.market_limit_break_alerts("600001")["data"]["states"] == {"600001": {}}
        with pytest.raises(Exception, match="6 位数字"):
            vr_app.market_limit_break_alerts("bad-code")
