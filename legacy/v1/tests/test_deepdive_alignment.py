"""A3 behavior tests; synthetic public inputs and no model/network calls."""
import json
import threading
from types import SimpleNamespace

import pytest

from review_agent.api import DeepDiveInput, Manager
from review_agent.runtime import Runtime, connection
from review_agent.store import Store
from duanxian.llm_errors import LlmConfigError


def manager(tmp_path):
    reviews = tmp_path / "reviews"
    reviews.mkdir(exist_ok=True)
    return Manager(Store(tmp_path / "state"), reviews, Runtime(tmp_path / "engine"))


def body(request_id="a" * 32, stock="600519"):
    return DeepDiveInput(stock=stock, request_id=request_id,
                        llm={"provider": "codex-private", "model": "gpt-5.3-codex-spark"})


def final():
    return {"code": "600519", "name": "测试标的", "trade_date": "2026-09-07",
            "theme_report": "题材资料已注明。", "capital_report": "资金资料有缺口。",
            "technical_report": "技术资料已注明。", "risk_report": "风险需要后续核对。",
            "verdict": "仅依据给定资料。", "verdict_struct": {"one_liner": "资料有待核验。"},
            "debate_state": {"join_history": "依据", "avoid_history": "缺口"}}


def submit(m, item=None):
    item = item or body()
    source, key = connection(item.llm.model_dump())
    with m.lock:
        row = m.deepdive.submit(item, source, key)
    m.deepdive.worker.join(timeout=5)
    assert not m.deepdive.busy()
    return row


def test_dd_source_stage_idempotency_and_old_archive(tmp_path, monkeypatch):
    m = manager(tmp_path)
    original = {"code": "000001", "verdict_md": "old report"}
    (m.deepdive.reports / "000001.json").write_text(json.dumps(original))
    calls = []
    def run(stock, date, **options):
        calls.append(options["llm"].source)
        options["progress"]("技术形态")
        assert m.deepdive.snapshot()["stage"] == "技术形态"
        return final()
    monkeypatch.setattr("duanxian.deepdive.graph.run", run)
    try:
        submit(m); submit(m)
        report = m.deepdive.report("a" * 32)
        assert calls == [report["ai_source"]]
        assert report["ai_source"]["model"] == "gpt-5.3-codex-spark"
        assert json.loads((m.deepdive.reports / "000001.json").read_text()) == original
        assert report["run_type"] == "stock_deepdive"
        assert m.deepdive.snapshot()["status"] == "complete"
    finally: m.shutdown()


@pytest.mark.parametrize("result", [{"error": "行情不可用"}, {**final(), "verdict_struct": None}])
def test_dd_failure_preserves_previous(tmp_path, monkeypatch, result):
    m = manager(tmp_path)
    old = b'{"code":"000001","verdict_md":"old"}'
    (m.deepdive.reports / "latest.json").write_bytes(old)
    monkeypatch.setattr("duanxian.deepdive.graph.run", lambda *a, **k: result)
    try:
        submit(m)
        assert m.deepdive.snapshot()["status"] == "failed"
        assert (m.deepdive.reports / "latest.json").read_bytes() == old
        assert not list(m.deepdive.history.glob("*.json"))
    finally: m.shutdown()


def test_deepdive_history_lists_every_immutable_version_newest_first(tmp_path):
    m = manager(tmp_path)
    versions = [
        ("a" * 32, "000993", "闽东电力", "2026-09-26 09:04 CST"),
        ("b" * 32, "002519", "银河电子", "2026-09-26 21:35 CST"),
        ("c" * 32, "000993", "闽东电力", "2026-09-27 20:21 CST"),
    ]
    try:
        for job_id, code, name, generated_at in versions:
            (m.deepdive.history / f"{job_id}.json").write_text(json.dumps({
                "job_id": job_id,
                "code": code,
                "name": name,
                "generated_at": generated_at,
                "trade_date": generated_at[:10],
                "ai_source": {"provider": "codex-private", "model": "gpt-test"},
                "verdict": {"one_liner": "ok"},
            }), encoding="utf-8")
        (m.deepdive.history / ("d" * 32 + ".json")).write_text("{broken", encoding="utf-8")

        all_reports = m.deepdive.list_reports(limit=20)
        code_reports = m.deepdive.list_reports(limit=20, code="000993")

        assert all_reports["total"] == 3
        assert [row["job_id"] for row in all_reports["reports"]] == ["c" * 32, "b" * 32, "a" * 32]
        assert code_reports["total"] == 2
        assert [row["job_id"] for row in code_reports["reports"]] == ["c" * 32, "a" * 32]
        assert all_reports["reports"][0]["ai_source"]["model"] == "gpt-test"

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from review_agent.api import create_router
        app = FastAPI()
        app.include_router(create_router(m, "history-test"))
        response = TestClient(app, base_url="http://127.0.0.1").get(
            "/api/review-agent/deepdive/reports?code=000993",
            headers={"Authorization": "Bearer history-test"},
        )
        assert response.status_code == 200
        assert response.json()["total"] == 2
    finally:
        m.shutdown()


def test_deepdive_delete_report_rebuilds_latest_indexes(tmp_path):
    m = manager(tmp_path)
    versions = [
        ("a" * 32, "000993", "闽东电力", "2026-09-26 09:04 CST"),
        ("b" * 32, "002519", "银河电子", "2026-09-26 21:35 CST"),
        ("c" * 32, "000993", "闽东电力", "2026-09-27 20:21 CST"),
    ]
    try:
        payloads = []
        for job_id, code, name, generated_at in versions:
            payload = {
                "job_id": job_id,
                "code": code,
                "name": name,
                "generated_at": generated_at,
                "trade_date": generated_at[:10],
                "ai_source": {"provider": "codex-private", "model": "gpt-test"},
                "verdict": {"one_liner": "ok"},
            }
            payloads.append(payload)
            (m.deepdive.history / f"{job_id}.json").write_text(json.dumps(payload), encoding="utf-8")
        (m.deepdive.reports / "latest.json").write_text(json.dumps(payloads[2]), encoding="utf-8")
        (m.deepdive.reports / "000993.json").write_text(json.dumps(payloads[2]), encoding="utf-8")
        (m.deepdive.reports / "002519.json").write_text(json.dumps(payloads[1]), encoding="utf-8")

        result = m.deepdive.delete_report("c" * 32)

        assert result["deleted_job_id"] == "c" * 32
        assert result["total"] == 2
        assert result["latest_job_id"] == "b" * 32
        assert not (m.deepdive.history / ("c" * 32 + ".json")).exists()
        assert json.loads((m.deepdive.reports / "latest.json").read_text())["job_id"] == "b" * 32
        assert json.loads((m.deepdive.reports / "000993.json").read_text())["job_id"] == "a" * 32
        assert json.loads((m.deepdive.reports / "002519.json").read_text())["job_id"] == "b" * 32

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from review_agent.api import create_router
        app = FastAPI()
        app.include_router(create_router(m, "delete-test"))
        response = TestClient(app, base_url="http://127.0.0.1").post(
            f"/api/review-agent/deepdive/reports/{'a' * 32}/delete",
            headers={"Authorization": "Bearer delete-test", "Content-Type": "application/json"},
            json={},
        )
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert not (m.deepdive.reports / "000993.json").exists()
    finally:
        m.shutdown()


def test_cancel_prevents_publish_and_source_switch_during_active(tmp_path, monkeypatch):
    from review_agent.evidence import EvidenceError
    m = manager(tmp_path)
    entered, release = threading.Event(), threading.Event()
    def run(*a, **options):
        entered.set(); release.wait(3); options["check"](); return final()
    monkeypatch.setattr("duanxian.deepdive.graph.run", run)
    item = body(); source, key = connection(item.llm.model_dump())
    try:
        with m.lock: m.deepdive.submit(item, source, key)
        assert entered.wait(2)
        with pytest.raises(EvidenceError): m.deepdive.submit(body("b" * 32), source, key)
        m.deepdive.cancel("a" * 32); release.set(); m.deepdive.worker.join(3)
        assert m.deepdive.snapshot()["status"] == "cancelled"
        assert not (m.deepdive.reports / "latest.json").exists()
    finally: release.set(); m.shutdown()


@pytest.mark.parametrize("factory", ["create_theme_analyst", "create_capital_analyst", "create_technical_analyst",
                                     "create_risk_analyst", "create_join_debator", "create_avoid_debator"])
def test_dd_config_and_cancel_errors_propagate(factory):
    from duanxian.deepdive import agents
    class LLM:
        def invoke(self, prompt): raise LlmConfigError("已取消")
    fake = SimpleNamespace(get_theme=lambda *a: "facts", get_profile=lambda *a: "facts",
                           get_lhb=lambda *a: "facts", get_kline=lambda *a: "facts")
    fn = getattr(agents, factory)
    node = fn(LLM(), fake) if factory.endswith("analyst") else fn(LLM())
    with pytest.raises(LlmConfigError, match="已取消"):
        node({"code": "600519", "name": "合成", "profile": "facts", "debate_state": {}})


def test_restart_marks_running_failed_without_rebilling(tmp_path):
    m = manager(tmp_path)
    path = m.deepdive.directory / ("c" * 32 + ".json")
    path.write_text(json.dumps({"job_id": "c" * 32, "started": 10, "running": True, "status": "running"}))
    m.shutdown()
    restored = manager(tmp_path)
    try:
        assert restored.deepdive.snapshot()["status"] == "failed"
        assert not restored.deepdive.busy()
    finally: restored.shutdown()
