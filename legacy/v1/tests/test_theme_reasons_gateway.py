from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from duanxian import app_settings
from duanxian import theme_reasons as gateway


@pytest.fixture
def isolated_gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "_CACHE_DIR", str(tmp_path / "reasons"))
    monkeypatch.setattr(gateway, "_STATUS_PATH", str(tmp_path / "status.json"))
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("IWENCAI_API_KEY", "test-key")
    monkeypatch.setattr(gateway.trade_calendar, "is_settled", lambda date: False)
    gateway.reset_memory_for_tests()
    yield gateway
    gateway.reset_memory_for_tests()


@pytest.mark.unit
def test_singleflight_reuses_one_upstream_result(isolated_gateway, monkeypatch):
    source = isolated_gateway
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def primary(date: str):
        calls.append(date)
        entered.set()
        assert release.wait(5)
        return {"600001": "机器人"}, None

    monkeypatch.setattr(source, "_fetch_iwencai", primary)
    monkeypatch.setattr(source, "_fetch_public", lambda date: pytest.fail("fallback must not run"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(source.get, "2026-09-01")
        # Importing the legacy fetchers module can take several seconds on a
        # cold WSL filesystem.  The assertion here is about single-flight, not
        # module-import speed, so leave enough headroom for that one-time cost.
        assert entered.wait(15)
        second = pool.submit(source.get, "2026-09-01")
        release.set()
        assert first.result()["reasons"] == second.result()["reasons"]
    assert calls == ["2026-09-01"]


@pytest.mark.unit
def test_close_fetches_one_final_then_keeps_it(isolated_gateway, monkeypatch):
    source = isolated_gateway
    settled = [False]
    calls: list[str] = []
    monkeypatch.setattr(source.trade_calendar, "is_settled", lambda date: settled[0])

    def primary(date: str):
        calls.append(date)
        return {"600001": f"题材{len(calls)}"}, None

    monkeypatch.setattr(source, "_fetch_iwencai", primary)
    intraday = source.get("20260901")
    assert intraday["settled"] is False
    settled[0] = True
    final = source.get("20260901")
    assert final["settled"] is True and final["reasons"]["600001"] == "题材2"
    assert source.get("20260901", force=True)["reasons"] == final["reasons"]
    assert len(calls) == 2


@pytest.mark.unit
def test_partial_primary_is_filled_without_overwriting_primary(isolated_gateway, monkeypatch):
    source = isolated_gateway
    monkeypatch.setattr(source, "_fetch_iwencai", lambda date: ({"600001": "主源"}, "部分返回"))
    monkeypatch.setattr(source, "_fetch_public", lambda date: ({"600001": "备用", "600002": "补齐"}, None))
    result = source.get("20260901")
    assert result["source"] == "mixed"
    assert result["reasons"] == {"600001": "主源", "600002": "补齐"}


@pytest.mark.unit
def test_iwencai_query_is_date_bound_and_reads_the_matching_reason_column(isolated_gateway, monkeypatch):
    from duanxian import fetchers
    import pandas as pd

    captured: list[str] = []

    class Client:
        def __init__(self, **_kwargs):
            self._session = type("Session", (), {"close": staticmethod(lambda: None)})()

        def query(self, query, page=1, limit=50):
            captured.append(query)
            return pd.DataFrame({
                "股票代码": ["600001.SH"],
                "涨停原因[20260902]": ["错误场次"],
                "涨停原因[20260901]": ["机器人+国企改革"],
            })

    monkeypatch.setattr(fetchers, "_iwencai_client_cls", lambda: Client)
    reasons, error = isolated_gateway._fetch_iwencai("2026-09-01")

    assert captured and "2026-09-01" in captured[0] and "今日" not in captured[0]
    assert reasons == {"600001": "机器人+国企改革"}
    assert error is None


@pytest.mark.unit
def test_breaker_skips_primary_across_callers(isolated_gateway, monkeypatch):
    source = isolated_gateway
    source._set_breaker("rate_limited", 15, None, "暂停 15 分钟")
    monkeypatch.setattr(source, "_fetch_iwencai", lambda date: pytest.fail("breaker must skip primary"))
    monkeypatch.setattr(source, "_fetch_public", lambda date: ({"600001": "公开源"}, None))
    result = source.get("20260901")
    assert result["source"] == "ths_public"
    assert source.current_status()["circuit_state"] == "rate_limited"


@pytest.mark.unit
def test_legacy_cache_is_read_and_new_cache_has_provenance(isolated_gateway, monkeypatch, tmp_path):
    source = isolated_gateway
    cache_dir = tmp_path / "reasons"
    cache_dir.mkdir()
    (cache_dir / "2026-08-29.json").write_text(json.dumps({
        "schema": 1, "date": "2026-08-29", "reasons": {"600001": "旧缓存"},
    }), encoding="utf-8")
    monkeypatch.setattr(source.trade_calendar, "is_settled", lambda date: True)
    result = source.get("2026-08-29")
    assert result["source"] == "legacy" and result["settled"] is True


@pytest.mark.unit
@pytest.mark.parametrize("value", [-1, 15, 90, 60.0, True, "60"])
def test_setting_rejects_values_outside_integer_enum(tmp_path, monkeypatch, value):
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    with pytest.raises(ValueError):
        app_settings.update_settings(value)


@pytest.mark.unit
@pytest.mark.parametrize("value", [0, 30, 60, 120])
def test_setting_round_trip_is_atomic(tmp_path, monkeypatch, value):
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    assert app_settings.update_settings(value)["iwencai_intraday_refresh_minutes"] == value
    assert app_settings.get_settings()["iwencai_intraday_refresh_minutes"] == value


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["auto", "direct", "proxy"])
def test_market_network_setting_round_trip(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    saved = app_settings.update_settings(
        60, market_network={"mode": mode, "proxy_url": "http://172.20.32.1:7890"},
    )
    assert saved["market_network"] == {"mode": mode, "proxy_url": "http://172.20.32.1:7890"}


@pytest.mark.unit
@pytest.mark.parametrize("proxy_url", [
    "socks5://172.20.32.1:7890",
    "http://user:secret@172.20.32.1:7890",
    "http://172.20.32.1:7890/path",
    "not-a-url",
])
def test_market_network_rejects_unsafe_proxy_urls(tmp_path, monkeypatch, proxy_url):
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    with pytest.raises(ValueError, match="代理地址"):
        app_settings.update_settings(60, market_network={"mode": "proxy", "proxy_url": proxy_url})


def test_scheduled_review_failure_is_persisted_for_settings_ui(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    app_settings.update_settings(60, {"enabled": True, "time": "18:30", "source": {"provider": "codex-private", "model": "gpt-test"}})
    app_settings.mark_review_schedule_run("2026-09-17", "2026-09-17 18:30:02 CST")
    app_settings.mark_review_schedule_result(
        "2026-09-17", "failed", "2026-09-17 18:56:46 CST", 1604,
        "RuntimeError: codex 生成超时",
    )

    schedule = app_settings.get_settings()["review_schedule"]
    assert schedule["last_status"] == "failed"
    assert schedule["last_elapsed"] == 1604
    assert schedule["last_error"] == "RuntimeError: codex 生成超时"


def test_scheduled_review_rejects_browser_only_api_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    with pytest.raises(ValueError, match="仅支持已测试"):
        app_settings.update_settings(60, {"enabled": True, "time": "18:30", "source": {"provider": "openai", "model": "gpt-test"}})
