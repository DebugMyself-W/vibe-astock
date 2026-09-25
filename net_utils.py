"""市场数据网络访问辅助。

统一处理「国内行情/东财接口默认直连，必要时显式走代理」这件事，避免各模块各写一套：
- 默认：不信任系统代理（不开代理时稳定直连）
- `VIBE_MARKET_PROXY=1` / `VR_DATA_PROXY=1`：显式信任系统代理
- `VIBE_MARKET_DIRECT=1`：与默认同向，强调即使系统里配了代理也强制直连

这里只约束**市场数据**请求；LLM / 其它外网流量不在这里改环境。
"""

from __future__ import annotations

import os
import importlib
import threading
import urllib.request

import requests

_TRUE = {"1", "true", "yes"}
_AKSHARE_REQUEST_PATCH_LOCK = threading.RLock()


class _RequestsWithDefaultTimeout:
    """Module-like proxy that adds a timeout without patching global requests."""

    def __init__(self, delegate, timeout: float):
        self._delegate = delegate
        self._timeout = timeout

    def get(self, *args, **kwargs):
        kwargs.setdefault("timeout", self._timeout)
        return self._delegate.get(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._delegate, name)


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


def market_trust_env() -> bool:
    """市场数据请求是否读取系统代理环境变量。"""
    if _flag("VIBE_MARKET_PROXY") or _flag("VR_DATA_PROXY"):
        return True
    if _flag("VIBE_MARKET_DIRECT"):
        return False
    return False


_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_STDLIB_URLOPEN = urllib.request.urlopen


def market_urlopen(req, timeout: int = 10):
    """按市场数据代理口径打开 URL/Request。"""
    # Respect an explicitly installed/monkeypatched urlopen hook.  This keeps
    # the standard-library seam observable for tests and runtime instrumentation,
    # while normal production calls still use the proxy-free opener below.
    if urllib.request.urlopen is not _STDLIB_URLOPEN:
        return urllib.request.urlopen(req, timeout=timeout)
    if market_trust_env():
        return urllib.request.urlopen(req, timeout=timeout)
    return _DIRECT_OPENER.open(req, timeout=timeout)


def market_session() -> requests.Session:
    """创建按市场数据代理口径配置的 requests.Session。"""
    s = requests.Session()
    s.trust_env = market_trust_env()
    return s


def call_with_requests_timeout(module_name: str, function_name: str, *args,
                               timeout: float = 8.0, **kwargs):
    """Call an AkShare source while supplying a missing ``requests`` timeout.

    Several AkShare functions use bare ``requests.get`` calls.  A broken route
    can therefore occupy a FastAPI worker forever.  Patch only that source
    module's ``requests`` binding, serialize the tiny patch window, and always
    restore it.  The global requests package and unrelated app traffic are not
    modified.
    """
    module = importlib.import_module(module_name)
    with _AKSHARE_REQUEST_PATCH_LOCK:
        original = module.requests
        module.requests = _RequestsWithDefaultTimeout(original, timeout)
        try:
            return getattr(module, function_name)(*args, **kwargs)
        finally:
            module.requests = original
