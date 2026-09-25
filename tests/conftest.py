"""测试全局约束：**禁止出站网络**。

本目录只测纯逻辑。一旦某条测试忘了 patch 数据源（比如 `latest_session()` 内部
会去打腾讯行情判断今天是否开市），它就会变成"依赖网络 + 依赖今天是不是交易日"
的脆弱测试——平时绿、周末或断网时红，而且失败原因看不出来。
这里直接把 connect 拦掉：谁漏了 patch 就当场以明确信息失败。
"""

from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def _isolate_followup_storage(tmp_path, monkeypatch):
    from duanxian import followup_store
    monkeypatch.setattr(followup_store, "DIR", str(tmp_path / "followups"))


@pytest.fixture(autouse=True)
def _isolate_reason_gateway(tmp_path, monkeypatch):
    """Reason-cache and quota tests must never touch the developer's real state."""
    from duanxian import app_settings, theme_reasons

    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(theme_reasons, "_CACHE_DIR", str(tmp_path / "zt_reasons"))
    monkeypatch.setattr(theme_reasons, "_STATUS_PATH", str(tmp_path / "iwencai_status.json"))
    theme_reasons.reset_memory_for_tests()
    yield
    theme_reasons.reset_memory_for_tests()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def blocked(self, *args, **kwargs):
        raise AssertionError(
            "测试期间不允许出站网络连接 —— 说明有数据源没被 patch，请补上 monkeypatch"
        )

    monkeypatch.setattr(socket.socket, "connect", blocked, raising=False)
