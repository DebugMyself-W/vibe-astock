from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.unit
def test_deepdive_theme_reuses_stock_research_concepts(monkeypatch):
    from duanxian.deepdive import data

    monkeypatch.setattr(data, "_stock_concepts", lambda code: (
        ["风电零部件", "电力设备", "风能"],
        [{"concept": "风能", "hit": 272}, {"concept": "次新股", "hit": 126}],
    ))
    monkeypatch.setattr(data, "_iwencai_theme", lambda code, name: "")
    monkeypatch.setattr(data, "agent_reach_search", lambda *args, **kwargs: "[未启用全网补充]")

    result = data.get_theme("603248", "锡华科技")

    assert "板块归属 · 概念：风电零部件、电力设备、风能" in result
    assert "当下热门概念命中：风能（热度 272）、次新股（热度 126）" in result


@pytest.mark.unit
def test_deepdive_iwencai_imports_client_from_vr(monkeypatch):
    from duanxian.deepdive import data

    # A tiny frame object with pandas-like columns/iloc behavior.
    class _Result:
        columns = ["所属概念"]
        iloc = [dict(所属概念="风能;次新股")]
        def __len__(self): return 1

    monkeypatch.setenv("IWENCAI_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "iwencai_client", SimpleNamespace(IwencaiClient=lambda: SimpleNamespace(query=lambda *a, **k: _Result())))
    expected = str(__import__("pathlib").Path(data.__file__).resolve().parents[2] / "vr")

    assert data._iwencai_theme("603248", "锡华科技") == "风能;次新股"
    assert expected in sys.path


@pytest.mark.unit
def test_iwencai_secret_override_is_private_and_can_disable_repo_env(tmp_path, monkeypatch):
    from duanxian import data_credentials

    monkeypatch.setattr(data_credentials, "_PATH", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("IWENCAI_API_KEY", "repo-env-key")

    data_credentials.save_iwencai_key("  configured-key  ")
    assert data_credentials.get_iwencai_key() == "configured-key"
    assert data_credentials.iwencai_key_status() == {"configured": True, "source": "settings"}
    assert "configured-key" not in str(data_credentials.iwencai_key_status())
    assert oct(os.stat(data_credentials._PATH).st_mode & 0o777) == "0o600"

    data_credentials.clear_iwencai_key()
    assert data_credentials.get_iwencai_key() == ""
    assert data_credentials.iwencai_key_status() == {"configured": False, "source": "settings"}


@pytest.mark.unit
def test_deepdive_worker_receives_iwencai_key(monkeypatch, tmp_path):
    from duanxian import data_credentials
    from review_agent import public_worker

    monkeypatch.setattr(data_credentials, "get_iwencai_key", lambda: "secret-for-worker")
    env = public_worker._worker_environment("get_theme", tmp_path)

    assert env["IWENCAI_API_KEY"] == "secret-for-worker"
    assert "IWENCAI_API_KEY" not in public_worker._worker_environment("get_profile", tmp_path)


@pytest.mark.unit
def test_settings_endpoint_updates_iwencai_key_without_echo(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from duanxian import data_credentials, theme_reasons
    import server

    monkeypatch.setattr(data_credentials, "_PATH", str(tmp_path / "credentials.json"))
    monkeypatch.setattr(theme_reasons, "reset_runtime_status", lambda: None)
    client = TestClient(server.app, base_url="http://127.0.0.1")

    saved = client.put("/api/settings/iwencai-key", json={"api_key": "new-secret"})
    assert saved.status_code == 200
    assert saved.json()["iwencai"]["configured"] is True
    assert "new-secret" not in saved.text
    assert data_credentials.get_iwencai_key() == "new-secret"

    cleared = client.delete("/api/settings/iwencai-key")
    assert cleared.status_code == 200
    assert cleared.json()["iwencai"]["configured"] is False


@pytest.mark.unit
def test_network_diagnostic_probes_real_eastmoney_concept_endpoint():
    import net_utils

    eastmoney = next(item for item in net_utils._DIAGNOSTIC_SOURCES if item[0] == "eastmoney")
    assert "push2.eastmoney.com/api/qt/slist/get" in eastmoney[2]
    assert "东财板块归属" == eastmoney[1]


@pytest.mark.unit
def test_stock_concepts_retry_same_algorithm_on_eastmoney_mirror(monkeypatch):
    import importlib

    astock = importlib.import_module("vr.astock")
    calls = []

    class _Response:
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload

    def fake_get(url, **_kwargs):
        calls.append(url)
        if "push2.eastmoney.com" in url:
            raise ConnectionError("transient reset")
        return _Response({"data": {"diff": {"0": {"f12": "BK0595", "f14": "风能", "f3": 1.2, "f128": ""}}}})

    monkeypatch.setattr(astock, "em_get", fake_get)
    result = astock.concept_blocks("603248")

    assert result["concept_tags"] == ["风能"]
    assert "push2.eastmoney.com" in calls[0]
    assert "push2test.eastmoney.com" in calls[1]


@pytest.mark.unit
def test_settings_ui_never_prefills_or_echoes_iwencai_secret():
    source = open("frontend/src/pages/Settings.tsx", encoding="utf-8").read()
    api_source = open("frontend/src/lib/api.ts", encoding="utf-8").read()
    assert 'type="password"' in source
    assert "saveIwencaiKey" in source and "clearIwencaiKey" in source
    assert 'request<AppSettings>("/settings/iwencai-key", "PUT"' in api_source
    assert "settings.iwencai.api_key" not in source
