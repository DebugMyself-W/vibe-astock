from fastapi.testclient import TestClient


def _route(server):
    return next(route for route in server.app.routes if getattr(route, "path", "") == "/api/kline")


def test_daily_kline_falls_back_after_primary_source_error(monkeypatch):
    import server

    astock = _route(server).endpoint.__globals__["astock"]
    expected = [{
        "datetime": "2026-10-09", "open": 11.39, "close": 12.53,
        "high": 12.53, "low": 11.39, "vol": 1000.0, "amount": 12530.0,
    }]
    monkeypatch.setattr(astock, "_mootdx_client", lambda: (_ for _ in ()).throw(RuntimeError("tdx unavailable")))
    monkeypatch.setattr(astock, "_tencent_daily_kline", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("HTTP 501")))
    monkeypatch.setattr(astock, "_akshare_tencent_daily_kline", lambda code, offset: expected)
    monkeypatch.setattr(astock, "_eastmoney_daily_kline", lambda code, offset: (_ for _ in ()).throw(AssertionError("third source should not run")))

    response = TestClient(server.app, base_url="http://127.0.0.1").get(
        "/api/kline?code=600825&category=4&offset=120"
    )

    assert response.status_code == 200
    assert response.json()["data"] == expected


def test_daily_kline_uses_eastmoney_as_third_source(monkeypatch):
    import server

    astock = _route(server).endpoint.__globals__["astock"]
    expected = [{
        "datetime": "2026-10-09", "open": 11.39, "close": 12.53,
        "high": 12.53, "low": 11.39, "vol": 1000.0, "amount": 12530.0,
    }]
    monkeypatch.setattr(astock, "_mootdx_client", lambda: (_ for _ in ()).throw(RuntimeError("tdx unavailable")))
    monkeypatch.setattr(astock, "_tencent_daily_kline", lambda *args, **kwargs: [])
    monkeypatch.setattr(astock, "_akshare_tencent_daily_kline", lambda code, offset: [])
    monkeypatch.setattr(astock, "_eastmoney_daily_kline", lambda code, offset: expected)

    response = TestClient(server.app, base_url="http://127.0.0.1").get(
        "/api/kline?code=600825&category=4&offset=120"
    )

    assert response.status_code == 200
    assert response.json()["data"] == expected
