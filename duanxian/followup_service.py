"""A single bounded collector, owned by the FastAPI lifespan, not the browser."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from . import followup, followup_data, followup_store as store, review_store, trade_calendar

logger = logging.getLogger(__name__)


def market_phase(now) -> str:
    h = now.strftime("%H:%M")
    if "09:25" <= h < "09:30":
        return "auction"
    if "09:30" <= h <= "11:30" or "13:00" <= h <= "15:00":
        return "live"
    if "15:05" <= h <= "18:00":
        return "close"
    return "idle"


class Collector:
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = None
        self.seen = {}
        self.close_attempts = {}

    def start(self):
        if os.getenv("VIBE_FOLLOWUP_ENABLED", "1") == "0" or self.thread is not None:
            return
        self.lock = store.FileLock(Path(store.DIR) / ".collector.lock")
        if not self.lock.acquire():
            logger.info("已有次日跟踪采集进程，本进程只提供读取")
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run, name="review-followup", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        # Never release the lease while an in-flight HTTP request is still running.
        if not self.thread and self.lock:
            self.lock.release()

    def run(self):
        try:
            while not self.stop_event.is_set():
                started = time.monotonic()
                try:
                    self.tick()
                except Exception:
                    logger.exception("次日跟踪采集失败，将在下一轮重试")
                self.stop_event.wait(max(1, 60 - (time.monotonic() - started)))
        finally:
            if self.lock:
                self.lock.release()
            self.thread = None

    def discover(self):
        for date in review_store.dates():
            path = Path(review_store.DIR) / f"{date}.json"
            key = path.stat().st_mtime_ns
            if self.seen.get(date) == key and store.load_prediction(date):
                continue
            review = review_store.load(date)
            if review:
                store.capture(review, date)
                self.seen[date] = key

    def tick(self, now=None):
        now = now or store.china_now()
        self.discover()
        days = followup_data.calendar_days(now)
        if not days:
            return
        current = now.date().isoformat()
        for directory in sorted(Path(store.DIR).glob("????-??-??")):
            if self.stop_event.is_set():
                return
            date = directory.name
            if store.read_json(directory / "final.json"):
                continue
            ev = store.load_session(date).get("eval_date") or followup_data.next_confirmed(date, days)
            if not ev:
                continue
            store.bind_session(date, ev, now)
            pred = store.active_prediction(date)
            if not pred:
                continue
            codes = sorted({s["code"] for s in pred["universe"]["stocks"]} |
                           {s["code"] for d in pred["directions"] for s in d["members"] + d["explicit_members"]})
            phase = market_phase(now)
            after_deadline = ev == current and now.strftime("%H:%M") > "18:00"
            final = ev < current or (ev == current and phase == "close") or after_deadline
            if ev > current or (ev == current and phase == "idle" and not after_deadline):
                continue
            if final:
                last = self.close_attempts.get(date)
                if last and (now - last).total_seconds() < (300 if ev == current else 3600):
                    continue
                self.close_attempts[date] = now
            try:
                if ev < current or after_deadline:
                    old = store.read_json(directory / "close-observation.json")
                    cached = followup_data.historical(ev, codes)
                    observation = cached or old
                    if old and cached:
                        observation = followup_data.merge_close_observations(cached, old)
                    if observation is None:
                        store.status(date, error="缺少目标日定稿资料，未使用当前行情补算", last_attempt_at=store.stamp(now))
                        continue
                else:
                    if trade_calendar.quote_trade_day() != ev:
                        raise ValueError("行情所属日期不符，暂停本轮判定")
                    observation = followup_data.live(ev, codes, now)
                if self.stop_event.is_set():
                    return
                if final:
                    # Closed observations are isolated from the existing settled-session caches.
                    with store.transaction():
                        observation = followup_data.merge_close_observations(
                            observation, store.read_json(directory / "close-observation.json"))
                        store.write_json(directory / "close-observation.json", observation)
                else:
                    observed = store.parse_time(observation["observed_at"])
                    if market_phase(observed) not in {"auction", "live"}:
                        continue
                result = followup.evaluate(pred, observation, final=final)
                exhausted = final and (ev < current or now.strftime("%H:%M") >= "17:55")
                any_data = any(r.get("value") is not None for r in followup.metric_readings(pred, observation, final=final)[0].values())
                any_data = any_data or any(d["stats"]["valid"] for d in result["directions"])
                if not any_data and not exhausted:
                    raise ValueError("本轮行情均不可用")
                errors = [s.get("reason", "") for s in observation.get("sources", {}).values() if s.get("available") is False]
                success = {"last_success_at": observation["observed_at"]} if any_data else {}
                store.status(date, last_attempt_at=store.stamp(now), **success,
                             error="；".join(errors), collecting=True)
                if not final:
                    store.record(date, result)
                elif result["complete"] or exhausted:
                    store.record(date, result, final=True)
                else:
                    missing = [r.get("label") or r["metric"] for r in result["items"] if not r["ready"]]
                    detail = "；".join(errors) or "、".join(missing) or "方向成员行情覆盖不足"
                    store.status(date, error=f"收盘数据待补齐：{detail}")
            except Exception as exc:
                logger.warning("跟踪 %s -> %s 失败: %s", date, ev, exc)
                store.status(date, last_attempt_at=store.stamp(now), error=str(exc))


collector = Collector()
