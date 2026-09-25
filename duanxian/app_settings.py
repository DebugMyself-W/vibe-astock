"""Application-wide persisted settings shared by every page and browser."""

from __future__ import annotations

import json
import os
import re
import threading

from .util import atomic_write_json

_PATH = os.path.expanduser("~/.duanxian-agents/settings.json")
_LOCK = threading.RLock()
_SCHEMA = 1
_DEFAULT_REFRESH = 60
ALLOWED_IWENCAI_REFRESH_MINUTES = {0, 30, 60, 120}
_DEFAULT_REVIEW_TIME = "15:30"


def _normalize_review_schedule(raw: object) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    enabled = bool(raw.get("enabled", False))
    run_time = str(raw.get("time") or _DEFAULT_REVIEW_TIME).strip()
    if not re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", run_time):
        run_time = _DEFAULT_REVIEW_TIME
    force = bool(raw.get("force", False))
    last_date = raw.get("last_date")
    if not isinstance(last_date, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", last_date):
        last_date = None
    last_triggered_at = raw.get("last_triggered_at")
    if not isinstance(last_triggered_at, str):
        last_triggered_at = None
    last_status = raw.get("last_status")
    if last_status not in {"running", "succeeded", "failed"}:
        last_status = None
    last_finished_at = raw.get("last_finished_at")
    if not isinstance(last_finished_at, str):
        last_finished_at = None
    last_error = raw.get("last_error")
    if not isinstance(last_error, str):
        last_error = None
    last_elapsed = raw.get("last_elapsed")
    if type(last_elapsed) is not int or last_elapsed < 0:
        last_elapsed = None
    return {
        "enabled": enabled,
        "time": run_time,
        "force": force,
        "last_date": last_date,
        "last_triggered_at": last_triggered_at,
        "last_status": last_status,
        "last_finished_at": last_finished_at,
        "last_error": last_error,
        "last_elapsed": last_elapsed,
    }


def _read() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError):
        raw = {}
    value = raw.get("iwencai_intraday_refresh_minutes", _DEFAULT_REFRESH)
    if type(value) is not int or value not in ALLOWED_IWENCAI_REFRESH_MINUTES:
        value = _DEFAULT_REFRESH
    return {
        "iwencai_intraday_refresh_minutes": value,
        "review_schedule": _normalize_review_schedule(raw.get("review_schedule")),
    }


def get_settings() -> dict:
    with _LOCK:
        return dict(_read())


def _write(settings: dict) -> dict:
    payload = {
        "schema": _SCHEMA,
        "iwencai_intraday_refresh_minutes": settings["iwencai_intraday_refresh_minutes"],
        "review_schedule": _normalize_review_schedule(settings.get("review_schedule")),
    }
    if not atomic_write_json(_PATH, payload):
        raise OSError("综合设置写入失败")
    return get_settings()


def update_settings(iwencai_intraday_refresh_minutes: int, review_schedule: dict | None = None) -> dict:
    if type(iwencai_intraday_refresh_minutes) is not int or iwencai_intraday_refresh_minutes not in ALLOWED_IWENCAI_REFRESH_MINUTES:
        raise ValueError("iwencai_intraday_refresh_minutes 只允许 0、30、60、120")
    with _LOCK:
        current = _read()
        current["iwencai_intraday_refresh_minutes"] = iwencai_intraday_refresh_minutes
        if review_schedule is not None:
            current["review_schedule"] = _normalize_review_schedule(review_schedule)
        return _write(current)


def mark_review_schedule_run(date: str, triggered_at: str) -> dict:
    with _LOCK:
        current = _read()
        schedule = _normalize_review_schedule(current.get("review_schedule"))
    schedule["last_date"] = date
    schedule["last_triggered_at"] = triggered_at
    schedule["last_status"] = "running"
    schedule["last_finished_at"] = None
    schedule["last_error"] = None
    schedule["last_elapsed"] = None
    current["review_schedule"] = schedule
    return _write(current)


def mark_review_schedule_result(date: str, status: str, finished_at: str, elapsed: int, error: str | None = None) -> dict:
    """Persist the outcome so a service restart cannot erase a failed scheduled run."""
    if status not in {"succeeded", "failed"}:
        raise ValueError("定时复盘状态只允许 succeeded 或 failed")
    with _LOCK:
        current = _read()
        schedule = _normalize_review_schedule(current.get("review_schedule"))
        # Do not let a late prior job overwrite the status of a newer trigger.
        if schedule.get("last_date") != date:
            return current
        schedule["last_status"] = status
        schedule["last_finished_at"] = finished_at
        schedule["last_elapsed"] = max(0, int(elapsed))
        schedule["last_error"] = (str(error)[:500] if status == "failed" and error else None)
        current["review_schedule"] = schedule
        return _write(current)
