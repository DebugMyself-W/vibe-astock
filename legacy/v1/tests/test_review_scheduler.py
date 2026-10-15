from __future__ import annotations

from types import SimpleNamespace

import pytest

import server
from duanxian import app_settings


class _Daily:
    def __init__(self, status: dict):
        self.status = status

    def snapshot(self) -> dict:
        return dict(self.status)


@pytest.fixture
def scheduled_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "_PATH", str(tmp_path / "settings.json"))
    app_settings.update_settings(60, {
        "enabled": True,
        "time": "18:30",
        "source": {"provider": "codex-private", "model": "gpt-test"},
    })
    app_settings.mark_review_schedule_run("2026-09-17", "2026-09-17 18:30:02 CST")
    return app_settings.get_settings()["review_schedule"]


def test_scheduled_status_tracks_matching_running_job(scheduled_settings):
    manager = SimpleNamespace(daily=_Daily({
        "date": "2026-09-17", "running": True, "status": "running", "elapsed": 15,
    }))
    assert server._finish_scheduled_status(manager, scheduled_settings) is True
    assert app_settings.get_settings()["review_schedule"]["last_status"] == "running"


def test_scheduled_status_persists_matching_completion(scheduled_settings):
    manager = SimpleNamespace(daily=_Daily({
        "date": "2026-09-17", "running": False, "status": "complete", "elapsed": 42,
    }))
    assert server._finish_scheduled_status(manager, scheduled_settings) is False
    schedule = app_settings.get_settings()["review_schedule"]
    assert schedule["last_status"] == "succeeded"
    assert schedule["last_elapsed"] == 42


def test_scheduled_status_does_not_leave_interrupted_job_running(scheduled_settings):
    manager = SimpleNamespace(daily=_Daily({
        "date": "2026-09-18", "running": False, "status": "failed", "elapsed": 0,
    }))
    assert server._finish_scheduled_status(manager, scheduled_settings) is False
    schedule = app_settings.get_settings()["review_schedule"]
    assert schedule["last_status"] == "failed"
    assert "未找到对应" in schedule["last_error"]
