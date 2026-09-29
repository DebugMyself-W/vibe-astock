from __future__ import annotations

import pytest
import requests

import net_utils


class _Response:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.cookies = {}
        self.closed = False

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, result):
        self.result = result

    def request(self, *_args, **_kwargs):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.unit
def test_auto_route_falls_back_from_restricted_direct_response(monkeypatch):
    policy = net_utils.MarketNetworkPolicy("auto", "auto", "http://proxy:7890", "settings", None)
    monkeypatch.setattr(net_utils, "_route_attempts", lambda route=None: (["direct", "proxy"], policy))
    direct = _Response(403)
    proxy = _Response(200)
    session = net_utils.MarketSession()
    monkeypatch.setattr(session, "_session", lambda route, _url: _FakeSession(direct if route == "direct" else proxy))

    result = session.get("https://example.test/data")

    assert direct.closed is True
    assert result is proxy
    assert result._vibe_market_route == "proxy"


@pytest.mark.unit
def test_auto_route_falls_back_from_direct_connection_error(monkeypatch):
    policy = net_utils.MarketNetworkPolicy("auto", "auto", "http://proxy:7890", "settings", None)
    monkeypatch.setattr(net_utils, "_route_attempts", lambda route=None: (["direct", "proxy"], policy))
    direct = requests.exceptions.ConnectionError("direct reset")
    proxy = _Response(200)
    session = net_utils.MarketSession()
    monkeypatch.setattr(session, "_session", lambda route, _url: _FakeSession(direct if route == "direct" else proxy))

    result = session.get("https://example.test/data")

    assert result is proxy
    assert result._vibe_market_route == "proxy"


@pytest.mark.unit
def test_proxy_mode_without_http_proxy_fails_loudly(monkeypatch):
    policy = net_utils.MarketNetworkPolicy("proxy", "proxy", "", None, None)
    monkeypatch.setattr(net_utils, "market_network_policy", lambda: policy)
    with pytest.raises(net_utils.MarketNetworkError, match="未检测到"):
        net_utils.market_session().get("https://example.test/data")
