from types import SimpleNamespace

import pytest


def test_target_day_indices_are_read_from_historical_kline(monkeypatch):
    from duanxian import fetchers

    calls = []

    def fake_get(_url, *, params, **_kwargs):
        code = params["param"].split(",", 1)[0]
        calls.append(code)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "data": {
                    code: {
                        "day": [
                            ["2026-09-23", "99", "100"],
                            ["2026-09-24", "100", "102"],
                        ]
                    }
                }
            },
        )

    monkeypatch.setattr(fetchers, "_direct_get", fake_get)

    result = fetchers.fetch_a_index_day("2026-09-24")

    assert result["available"] is True
    assert result["date"] == "2026-09-24"
    assert result["source"] == "tencent_daily_kline"
    assert calls == [code for code, _name in fetchers.A_INDICES]
    assert all(item["change_pct"] == 2.0 for item in result["items"])


@pytest.mark.parametrize(
    ("reader", "expected_groups"),
    [
        ("get_capital_data", False),
        ("get_macro_sector_data", True),
    ],
)
def test_latest_session_primary_failure_uses_dated_history(
    monkeypatch, reader, expected_groups
):
    from duanxian import data, historical_sources, trade_calendar

    monkeypatch.setattr(
        trade_calendar,
        "live_quotes_are_close_of",
        lambda _date: (True, "目标日仍是最近收盘日"),
    )

    def fail_primary(_kind):
        raise ConnectionError("upstream closed connection")

    observed = []

    def fake_history(date, groups=None):
        observed.append((date, groups))
        return f"{date} 历史板块成交与涨跌（已核对资料日）"

    monkeypatch.setattr(data.dr, "fetch_sector_flow", fail_primary)
    monkeypatch.setattr(historical_sources, "historical_activity", fake_history)

    result = getattr(data, reader)("2026-09-24")

    assert "2026-09-24 历史板块成交与涨跌" in result
    assert "ConnectionError" not in result
    assert len(observed) == 1
    assert (observed[0][1] is not None) is expected_groups
