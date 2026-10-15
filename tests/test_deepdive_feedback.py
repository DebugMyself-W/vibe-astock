from types import SimpleNamespace

import pytest


def test_numeric_deepdive_resolution_never_waits_for_full_market_catalog(monkeypatch):
    from duanxian.deepdive import data

    monkeypatch.setattr(data, "_local_name_code_map", lambda: {})
    monkeypatch.setattr(data, "_name_code_map", lambda: (_ for _ in ()).throw(AssertionError("catalog must not run")))
    monkeypatch.setattr(data, "_zt_name_map", lambda: (_ for _ in ()).throw(AssertionError("limit pool must not run")))

    assert data.resolve("605577") == ("605577", "605577")


def test_numeric_deepdive_uses_name_from_the_quote_already_fetched():
    from duanxian.deepdive.graph import _resolved_name

    profile = "行情时间 20261012150000；名称 龙版传媒｜现价 14.03｜涨跌幅 -6.28%"
    assert _resolved_name("605577", "605577", profile) == "龙版传媒"


def test_frozen_inputs_expose_failing_public_stage(tmp_path, monkeypatch):
    from review_agent.deepdive import FrozenStockInputs
    from review_agent.evidence import EvidenceError

    stages = []
    monkeypatch.setattr(
        "review_agent.public_worker.fetch_public",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(EvidenceError("公开资料取数未完整完成；请检查网络或稍后重试")),
    )
    inputs = FrozenStockInputs(tmp_path, lambda: 30, stages.append)

    with pytest.raises(EvidenceError, match="解析标的失败"):
        inputs.resolve("605577")
    assert stages == ["解析标的"]


def test_full_valuation_keeps_quote_when_optional_forecast_network_fails(monkeypatch):
    from vr import astock

    monkeypatch.setattr(astock, "tencent_quote", lambda _codes: {"605577": {
        "name": "龙版传媒", "price": 14.03, "quote_time": "20261012150000",
        "mcap_yi": 62.0, "pe_ttm": 20.0, "pb": 2.1,
    }})
    monkeypatch.setattr(astock, "profit_forecast", lambda _code: (_ for _ in ()).throw(TimeoutError("proxy timeout")))

    result = astock.full_valuation("605577")

    assert result["name"] == "龙版传媒"
    assert result["price"] == 14.03
    assert "一致预期取数失败" in result["forecast_note"]
