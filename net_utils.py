"""Market-data-only network policy and diagnostics.

This module is the single HTTP seam for quotes, review data and research data.
It never mutates process proxy variables, so Codex/Claude/LLM traffic remains
owned by its own client and environment.
"""

from __future__ import annotations

import importlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

_TRUE = {"1", "true", "yes"}
_AKSHARE_REQUEST_PATCH_LOCK = threading.RLock()
_LAST_ROUTE_LOCK = threading.Lock()
_LAST_ROUTE: dict[str, object] = {"route": None, "at": None}
_DIAGNOSTIC_EM_LOCK = threading.Lock()
_DIAGNOSTIC_EM_LAST = [0.0]
_STDLIB_URLOPEN = urllib.request.urlopen
_RETRY_HTTP_CODES = {403, 407, 408, 425, 429, 500, 502, 503, 504}


class MarketNetworkError(RuntimeError):
    """The selected market-data route cannot be used."""


@dataclass(frozen=True)
class MarketNetworkPolicy:
    configured_mode: str
    effective_mode: str
    proxy_url: str
    proxy_source: str | None
    override_source: str | None


class _RequestsWithDefaultTimeout:
    """Module-like proxy that adds a timeout without patching global requests."""

    def __init__(self, delegate, timeout: float):
        self._delegate = delegate
        self._timeout = timeout

    def get(self, *args, **kwargs):
        kwargs.setdefault("timeout", self._timeout)
        return self._delegate.get(*args, **kwargs)

    def post(self, *args, **kwargs):
        kwargs.setdefault("timeout", self._timeout)
        return self._delegate.post(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._delegate, name)


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


def _valid_proxy_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def _stored_market_network() -> dict:
    try:
        from duanxian import app_settings

        value = app_settings.get_settings().get("market_network") or {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _detected_proxy(stored_url: str = "") -> tuple[str, str | None]:
    candidates = [
        (stored_url, "settings"),
        (os.environ.get("VIBE_MARKET_PROXY_URL", ""), "VIBE_MARKET_PROXY_URL"),
        (os.environ.get("https_proxy", ""), "https_proxy"),
        (os.environ.get("HTTPS_PROXY", ""), "HTTPS_PROXY"),
        (os.environ.get("http_proxy", ""), "http_proxy"),
        (os.environ.get("HTTP_PROXY", ""), "HTTP_PROXY"),
        (os.environ.get("all_proxy", ""), "all_proxy"),
        (os.environ.get("ALL_PROXY", ""), "ALL_PROXY"),
    ]
    for raw, source in candidates:
        value = str(raw or "").strip()
        if value and _valid_proxy_url(value):
            return value, source
    return "", None


def market_network_policy() -> MarketNetworkPolicy:
    stored = _stored_market_network()
    configured = str(stored.get("mode") or "auto").lower()
    if configured not in {"auto", "direct", "proxy"}:
        configured = "auto"
    effective = configured
    override = None
    # Legacy deployment flags remain authoritative. Proxy wins when both are set.
    if _flag("VIBE_MARKET_PROXY") or _flag("VR_DATA_PROXY"):
        effective, override = "proxy", "VIBE_MARKET_PROXY/VR_DATA_PROXY"
    elif _flag("VIBE_MARKET_DIRECT"):
        effective, override = "direct", "VIBE_MARKET_DIRECT"
    proxy_url, proxy_source = _detected_proxy(str(stored.get("proxy_url") or ""))
    return MarketNetworkPolicy(configured, effective, proxy_url, proxy_source, override)


def _safe_proxy_label(url: str) -> str:
    if not url:
        return "未检测到"
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}"
    except ValueError:
        return "已配置（格式异常）"


def _record_route(route: str) -> None:
    with _LAST_ROUTE_LOCK:
        _LAST_ROUTE.update({"route": route, "at": time.strftime("%Y-%m-%d %H:%M:%S")})


def market_network_status() -> dict:
    policy = market_network_policy()
    with _LAST_ROUTE_LOCK:
        last = dict(_LAST_ROUTE)
    return {
        "configured_mode": policy.configured_mode,
        "effective_mode": policy.effective_mode,
        "override_source": policy.override_source,
        "proxy_available": bool(policy.proxy_url),
        "proxy_source": policy.proxy_source,
        "proxy_label": _safe_proxy_label(policy.proxy_url),
        "last_route": last["route"],
        "last_route_at": last["at"],
        "ai_isolated": True,
    }


def market_trust_env() -> bool:
    """Backward-compatible view: true only when proxy is the forced route."""
    return market_network_policy().effective_mode == "proxy"


def _route_attempts(route: str | None = None) -> tuple[list[str], MarketNetworkPolicy]:
    policy = market_network_policy()
    selected = policy.effective_mode if route in {None, "effective"} else route
    if selected not in {"auto", "direct", "proxy"}:
        raise ValueError("route 只允许 effective、auto、direct 或 proxy")
    if selected == "direct":
        return ["direct"], policy
    if selected == "proxy":
        if not policy.proxy_url:
            raise MarketNetworkError("市场数据已选择代理模式，但未检测到可用的 HTTP(S) 代理地址")
        return ["proxy"], policy
    return (["direct", "proxy"] if policy.proxy_url else ["direct"]), policy


def _urllib_opener(route: str, proxy_url: str):
    proxies = {} if route == "direct" else {"http": proxy_url, "https": proxy_url}
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies))


def market_urlopen(req, timeout: int | float = 10, *, route: str | None = None):
    """Open a market-data URL using the selected route, with auto fallback."""
    if route is None and urllib.request.urlopen is not _STDLIB_URLOPEN:
        return urllib.request.urlopen(req, timeout=timeout)
    attempts, policy = _route_attempts(route)
    last: Exception | None = None
    for index, candidate in enumerate(attempts):
        try:
            response = _urllib_opener(candidate, policy.proxy_url).open(req, timeout=timeout)
            _record_route(candidate)
            return response
        except urllib.error.HTTPError as exc:
            last = exc
            if index + 1 >= len(attempts) or exc.code not in _RETRY_HTTP_CODES:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if index + 1 >= len(attempts):
                raise
    raise last or MarketNetworkError("市场数据请求没有可用路由")


class MarketSession:
    """Small requests-compatible Adapter implementing the market route policy."""

    def __init__(self, route: str | None = None):
        self.route = route
        self.headers = requests.structures.CaseInsensitiveDict()
        self.cookies = requests.cookies.RequestsCookieJar()
        self._sessions: dict[str, requests.Session] = {}

    def _session(self, candidate: str, proxy_url: str) -> requests.Session:
        if candidate not in self._sessions:
            session = requests.Session()
            session.trust_env = False
            if candidate == "proxy":
                session.proxies.update({"http": proxy_url, "https": proxy_url})
            self._sessions[candidate] = session
        session = self._sessions[candidate]
        if candidate == "proxy":
            session.proxies.clear()
            session.proxies.update({"http": proxy_url, "https": proxy_url})
        session.headers.update(self.headers)
        session.cookies.update(self.cookies)
        return session

    def request(self, method: str, url: str, **kwargs):
        attempts, policy = _route_attempts(self.route)
        last: Exception | None = None
        for index, candidate in enumerate(attempts):
            try:
                response = self._session(candidate, policy.proxy_url).request(method, url, **kwargs)
                self.cookies.update(response.cookies)
                if index + 1 < len(attempts) and response.status_code in _RETRY_HTTP_CODES:
                    response.close()
                    continue
                response._vibe_market_route = candidate  # type: ignore[attr-defined]
                _record_route(candidate)
                return response
            except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as exc:
                last = exc
                if index + 1 >= len(attempts):
                    raise
        raise last or MarketNetworkError("市场数据请求没有可用路由")

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        for session in self._sessions.values():
            session.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False

    def __getattr__(self, name):
        if hasattr(requests, name):
            return getattr(requests, name)
        raise AttributeError(name)


def market_session(*, route: str | None = None) -> MarketSession:
    return MarketSession(route=route)


def call_with_requests_timeout(module_name: str, function_name: str, *args,
                               timeout: float = 8.0, **kwargs):
    """Call an AkShare source through the market seam with a default timeout."""
    module = importlib.import_module(module_name)
    with _AKSHARE_REQUEST_PATCH_LOCK, market_session() as session:
        original = module.requests
        module.requests = _RequestsWithDefaultTimeout(session, timeout)
        try:
            return getattr(module, function_name)(*args, **kwargs)
        finally:
            module.requests = original


_DIAGNOSTIC_SOURCES = (
    ("tencent", "腾讯行情", "https://qt.gtimg.cn/q=sh000001", "gbk"),
    ("sina", "新浪行情", "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount?node=hs_a", "utf-8"),
    ("eastmoney", "东财板块归属", "https://push2.eastmoney.com/api/qt/slist/get?fltt=2&invt=2&secid=1.600519&spt=3&pi=0&pz=10&po=1&fields=f12,f14,f3,f128", "utf-8"),
    ("eastmoney_width", "东财市场宽度", "https://push2delay.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids=1.000001,0.399001&fields=f1,f2,f3,f6,f104,f105,f106", "utf-8"),
)


def _validate_diagnostic(source: str, raw: bytes, encoding: str) -> str:
    text = raw.decode(encoding, "replace").strip()
    if source == "tencent" and "~" in text and "sh000001" in text:
        return "返回上证指数快照"
    if source == "sina" and any(ch.isdigit() for ch in text):
        return "返回沪深 A 股数量"
    if source == "eastmoney":
        data = json.loads(text)
        if ((data.get("data") or {}).get("diff")):
            return "返回个股板块/概念归属"
    if source == "eastmoney_width":
        data = json.loads(text)
        diff = ((data.get("data") or {}).get("diff")) or []
        if len(diff) >= 2 and all(
                isinstance(row, dict) and all(key in row for key in ("f6", "f104", "f105", "f106"))
                for row in diff):
            return "返回沪深涨跌家数与成交额"
    raise ValueError("响应结构不符合预期")


def _diagnostic_fetch(source: str, request: urllib.request.Request, timeout: float, route: str) -> bytes:
    """Fetch one probe while respecting EastMoney's minimum request interval."""
    if not source.startswith("eastmoney"):
        with market_urlopen(request, timeout=timeout, route=route) as response:
            return response.read(128 * 1024)
    with _DIAGNOSTIC_EM_LOCK:
        wait = 1.2 - (time.monotonic() - _DIAGNOSTIC_EM_LAST[0])
        if wait > 0:
            time.sleep(wait)
        try:
            with market_urlopen(request, timeout=timeout, route=route) as response:
                return response.read(128 * 1024)
        finally:
            _DIAGNOSTIC_EM_LAST[0] = time.monotonic()


def diagnose_market_network(timeout: float = 5.0) -> dict:
    """Probe direct and Clash/system-proxy routes without changing saved mode."""
    policy = market_network_policy()
    routes = ["direct"] + (["proxy"] if policy.proxy_url else [])
    results = []
    for route in routes:
        sources = []
        for key, label, url, encoding in _DIAGNOSTIC_SOURCES:
            started = time.monotonic()
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                raw = _diagnostic_fetch(key, request, timeout, route)
                detail = _validate_diagnostic(key, raw, encoding)
                sources.append({"source": key, "label": label, "ok": True,
                                "latency_ms": round((time.monotonic() - started) * 1000), "detail": detail})
            except Exception as exc:  # diagnostics must report every source
                sources.append({"source": key, "label": label, "ok": False,
                                "latency_ms": round((time.monotonic() - started) * 1000),
                                "detail": f"{type(exc).__name__}: {str(exc)[:180]}"})
        results.append({"route": route, "ok": all(item["ok"] for item in sources), "sources": sources})
    if not policy.proxy_url:
        results.append({
            "route": "proxy", "ok": False, "skipped": True, "sources": [],
            "detail": "未检测到 HTTP(S) 代理；请填写 Windows Clash 的 WSL 可访问地址，或设置 HTTP(S)_PROXY。",
        })
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": market_network_status(),
        "routes": results,
        "tcp_note": "通达信与 Baostock 使用 TCP，不经过 HTTP/Clash 代理；其失败会继续由各数据源独立降级并显式报告。",
    }
