"""Versioned, local-only storage for next-session observations."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .util import atomic_write_json, china_now, validate_trade_date

logger = logging.getLogger(__name__)
DIR = os.path.expanduser("~/.duanxian-agents/followups")
SCHEMA = 1
_mutex = threading.RLock()
_local = threading.local()


class LockedPrediction(ValueError):
    pass


class FileLock:
    """OS lock, released by the OS on crash; no stale PID-file recovery."""

    def __init__(self, path: Path):
        self.path, self.file = path, None

    def acquire(self, blocking: bool = False) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            return True
        except OSError:
            self.file.close()
            self.file = None
            return False

    def release(self):
        if self.file is not None:
            self.file.close()
            self.file = None


@contextmanager
def transaction():
    with _mutex:
        if getattr(_local, "writing", False):
            yield
            return
        lock = FileLock(Path(DIR) / ".write.lock")
        if not lock.acquire(blocking=True):
            raise RuntimeError("跟踪档案正在写入，请重试")
        try:
            _local.writing = True
            yield
        finally:
            _local.writing = False
            lock.release()


def read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_json(path: Path, data: dict):
    if not atomic_write_json(str(path), data):
        raise RuntimeError("跟踪档案写入失败")


def folder(date: str) -> Path:
    return Path(DIR) / validate_trade_date(date)


def stamp(now=None) -> str:
    return (now or china_now()).isoformat(timespec="seconds")


def parse_time(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=china_now().tzinfo)
    except (ValueError, TypeError):
        return None


def cutoff(eval_date: str) -> str:
    return f"{eval_date}T09:15:00+08:00"


def load_prediction(date: str) -> dict | None:
    return read_json(folder(date) / "prediction.json")


def load_session(date: str) -> dict:
    return read_json(folder(date) / "session.json") or {}


def editable(date: str, now=None, eval_date: str | None = None) -> bool:
    now = now or china_now()
    ev = eval_date or load_session(date).get("eval_date")
    if ev:
        return now < parse_time(cutoff(ev))
    # Unknown future calendar is not replaced with a weekday guess.
    return now.date().isoformat() == date or now.strftime("%H:%M") < "09:15"


def assert_editable(date: str, now=None, eval_date=None):
    if not editable(date, now, eval_date):
        raise LockedPrediction("原条件和名单已锁定，或下一交易日尚未确认；可追加盘中备注")


def normalized_tag(s: str) -> str:
    return re.sub(r"[\W_]+", "", s, flags=re.UNICODE).casefold()


def automatic_tags(direction: dict, themes: dict[str, dict]) -> tuple[list[str], str]:
    """Resolve a direction without semantic guessing.

    New reports carry ``theme_tags`` chosen from the frozen universe. Older
    reports can still safely resolve a literal tag, or a slash/comma-separated
    list of literal tags (for example ``汽车零部件/智能驾驶``). We deliberately
    do not match substrings such as ``电网设备修复`` to ``电网设备``.
    """
    declared = direction.get("theme_tags")
    if isinstance(declared, list):
        selected = list(dict.fromkeys(str(tag) for tag in declared if str(tag) in themes))
        if selected:
            return selected, "冻结标签"
    name = str(direction.get("direction") or "")
    exact = [tag for tag in themes if normalized_tag(tag) == normalized_tag(name)]
    if len(exact) == 1:
        return exact, "方向全等"
    parts = [part.strip() for part in re.split(r"[／/、，,+＋|｜]", name) if part.strip()]
    if len(parts) >= 2:
        selected = []
        for part in parts:
            matches = [tag for tag in themes if normalized_tag(tag) == normalized_tag(part)]
            if len(matches) == 1:
                selected.append(matches[0])
        selected = list(dict.fromkeys(selected))
        if selected:
            return selected, "组合名称拆分"
    return [], ""


def cached_universe(date: str, review: dict) -> dict:
    """Read original-session caches only; never fetch today's theme membership."""
    from . import market_facts, theme_reasons, theme_tree

    pool = read_json(Path(market_facts._CACHE_DIR) / f"{date}.json") or {}
    # 题材串缓存已迁到统一网关 theme_reasons；这里必须只读原场次缓存，
    # 不能为建立次日跟踪而重新请求当天题材成员。
    reasons = read_json(Path(theme_reasons._CACHE_DIR) / f"{date}.json") or {}
    valid_pool = pool.get("date") == date and pool.get("schema") in market_facts._FACTS_SCHEMA_READABLE
    rows = (pool.get("pools") or {}).get("zt") if valid_pool else None
    tags: dict[str, dict] = {}
    members = {}
    reason_map = reasons.get("reasons", {}) if reasons.get("date") == date else {}
    if isinstance(rows, list):
        for row in rows:
            code = str(row.get("code") or "")
            if not re.fullmatch(r"\d{6}", code):
                continue
            item = {"code": code, "name": row.get("name", code), "boards": row.get("boards"),
                    "sector": row.get("sector") or ""}
            members[code] = item
            for tag in theme_tree._tags(reason_map.get(code, "")):
                tags.setdefault(tag, {"tag": tag, "members": [], "complete": True})["members"].append(item)
    # Old snapshots may have only truncated tree members. Keep them explicitly partial.
    for theme in ((review.get("market_facts") or {}).get("theme_tree") or {}).get("themes", []):
        tag = theme.get("tag")
        if tag and tag not in tags:
            tags[tag] = {"tag": tag, "members": theme.get("members") or [], "complete": False}
    return {"stocks": list(members.values()), "complete": isinstance(rows, list),
            "themes": list(tags.values())}


def resolve_directions(focus: dict, universe: dict, bindings: dict) -> list[dict]:
    themes = {t["tag"]: t for t in universe["themes"]}
    stocks = {s["code"]: s for s in universe["stocks"]}
    out = []
    for i, direction in enumerate(focus.get("focus_directions") or []):
        key = str(i)
        automatic, source = automatic_tags(direction, themes)
        selected = bindings.get(key, automatic)
        source = "人工确认" if key in bindings else source
        members = {s["code"]: s for tag in selected for s in themes[tag]["members"]}
        explicit, unresolved = {}, []
        for name in direction.get("leader_candidates") or []:
            candidates = [s for s in stocks.values() if s["name"] == name or s["code"] == name
                          or re.search(r"(?<!\d)" + re.escape(s["code"]) + r"(?!\d)", str(name))]
            if len(candidates) == 1:
                explicit[candidates[0]["code"]] = candidates[0]
            else:
                unresolved.append(name)
        out.append({**direction, "id": key, "tags": selected, "binding_source": source,
                    "members": list(members.values()),
                    "explicit_members": list(explicit.values()), "unresolved": unresolved,
                    "complete": bool(selected) and all(themes[t]["complete"] for t in selected),
                    "binding_reason": "" if selected else "未关联 N 日题材，可在锁定前确认"})
    return out


def with_binding_suggestions(prediction: dict | None) -> dict | None:
    """Add read-only candidates to old locked predictions without rewriting them."""
    if not isinstance(prediction, dict):
        return prediction
    universe = prediction.get("universe") or {}
    themes = {t.get("tag"): t for t in universe.get("themes") or [] if isinstance(t, dict) and t.get("tag")}
    if not themes:
        return prediction
    result = copy.deepcopy(prediction)
    for direction in result.get("directions") or []:
        if direction.get("tags"):
            continue
        suggested, source = automatic_tags(direction, themes)
        if suggested:
            direction["suggested_tags"] = suggested
            direction["suggestion_source"] = source
    return result


def capture(review: dict, date: str, *, force=False, user_items=None, bindings=None, now=None) -> dict:
    """Persist observed-at time, never trust the report's generated_at as provenance."""
    from . import verification

    now = now or china_now()
    with transaction():
        old = load_prediction(date)
        if old and not editable(date, now):
            return old
        focus = copy.deepcopy(review.get("focus") or {})
        universe = cached_universe(date, review)
        if user_items is None:
            user_items = verification.load_user_items(date)
        combined = {x["metric"]: dict(x, by="ai") for x in focus.get("verification_items") or []}
        for item in user_items:
            combined[item["metric"]] = dict(item, by="user")
        items = verification.describe_items(list(combined.values()), review.get("emotion_metrics") or {},
                                            review.get("market_facts") or {})
        for item in items:
            group = {"money_effect_median": ("emotion_metrics", "money_effect"),
                     "deep_loss_count": ("market_facts", "loss_effect"),
                     "promotion_1to2": ("emotion_metrics", "promotion")}.get(item["metric"])
            meta = (review.get(group[0]) or {}).get(group[1]) or {} if group else {}
            item["base_complete"] = not meta.get("partial", False) and meta.get("coverage_rate", 1) == 1
        selected = bindings if bindings is not None else (old or {}).get("bindings", {})
        selected = {k: [t for t in tags if t in {v["tag"] for v in universe["themes"]}]
                    for k, tags in selected.items()}
        content = {"schema": SCHEMA, "calculation_version": 1, "prediction_date": date,
                   "focus": focus, "focus_md": review.get("focus_md") or "", "items": items, "universe": universe, "bindings": selected,
                   "directions": resolve_directions(focus, universe, selected),
                   "baseline_sample_date": (review.get("emotion_metrics") or {}).get("prev_date")}
        digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if old and old.get("digest") == digest and not force:
            return old
        version = f"{now.strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex[:8]}"
        result = {**content, "version": version, "digest": digest, "recorded_at": stamp(now)}
        write_json(folder(date) / "versions" / f"{version}.json", result)
        write_json(folder(date) / "prediction.json", result)
        return result


def capture_saved(review: dict, date: str):
    # A tracking failure must not turn a successfully saved AI report into a failed job.
    try:
        capture(review, date)
    except Exception:
        logger.exception("保存次日跟踪版本失败: %s", date)


def bind_session(date: str, eval_date: str, now=None) -> dict:
    with transaction():
        old = load_session(date)
        if old.get("eval_date"):
            if old["eval_date"] != eval_date:
                raise ValueError("已确认的下一交易日不可改变")
            return old
        versions = [read_json(p) for p in sorted((folder(date) / "versions").glob("*.json"))]
        before = [v for v in versions if v and parse_time(v.get("recorded_at"))
                  and parse_time(v["recorded_at"]) < parse_time(cutoff(eval_date))]
        prediction = before[-1] if before else load_prediction(date)
        result = {"eval_date": eval_date, "cutoff_at": cutoff(eval_date),
                  "eligible": bool(before), "prediction_version": (prediction or {}).get("version"),
                  "confirmed_at": stamp(now)}
        write_json(folder(date) / "session.json", result)
        return result


def active_prediction(date: str) -> dict | None:
    session = load_session(date)
    # Before cutoff edits still apply; after cutoff select the last pre-cutoff version.
    if session and not editable(date):
        versions = [read_json(p) for p in sorted((folder(date) / "versions").glob("*.json"))]
        before = [v for v in versions if v and parse_time(v.get("recorded_at"))
                  and parse_time(v["recorded_at"]) < parse_time(session["cutoff_at"])]
        if before:
            return before[-1]
    return load_prediction(date)


def save_bindings(date: str, bindings: dict) -> dict:
    from . import review_store

    with transaction():
        assert_editable(date)
        pred = load_prediction(date)
        if not pred:
            raise ValueError("跟踪档案尚未建立")
        if not isinstance(bindings, dict):
            raise ValueError("方向关联格式错误")
        ids = {d["id"] for d in pred["directions"]}
        tags = {t["tag"] for t in pred["universe"]["themes"]}
        for key, values in bindings.items():
            if key not in ids or not isinstance(values, list) or any(not isinstance(t, str) or t not in tags for t in values):
                raise ValueError("只能选择 N 日档案中的方向和题材")
        review = review_store.load(date)
        if not review:
            raise ValueError("复盘不存在")
        return capture(review, date, bindings={**pred["bindings"], **bindings})


def append_note(date: str, text: str) -> dict:
    if not isinstance(text, str):
        raise ValueError("备注必须为文字")
    text = text.strip()
    if not text or len(text) > 2000:
        raise ValueError("备注需为 1-2000 字")
    with transaction():
        if not load_prediction(date):
            raise ValueError("跟踪档案尚未建立")
        path = folder(date) / "notes.json"
        notes = (read_json(path) or {}).get("items", [])
        notes.append({"id": uuid.uuid4().hex, "text": text, "created_at": stamp()})
        write_json(path, {"items": notes})
        return notes[-1]


def history_key(dt: datetime) -> str:
    return dt.replace(minute=dt.minute // 30 * 30).strftime("%Y%m%dT%H%M")


def record(date: str, snapshot: dict, *, final=False):
    with transaction():
        if final:
            path = folder(date) / "final.json"
        else:
            dt = parse_time(snapshot["observed_at"])
            write_json(folder(date) / "latest.json", snapshot)
            path = folder(date) / "snapshots" / f"{history_key(dt)}.json"
        if path.exists():
            return
        write_json(path, snapshot)


def status(date: str, **fields):
    with transaction():
        path = folder(date) / "status.json"
        write_json(path, {**(read_json(path) or {}), **fields})


def get(date: str, at: str | None = None) -> dict:
    now = china_now()
    pred = active_prediction(date)
    session = load_session(date)
    paths = sorted((folder(date) / "snapshots").glob("*.json"))
    history = {}
    for path in paths:
        try:
            key = history_key(datetime.strptime(path.stem, "%Y%m%dT%H%M"))
        except ValueError:
            continue
        history.setdefault(key, path)
    times = list(history)
    final = read_json(folder(date) / "final.json")
    closing = None
    if not final and pred:
        from . import followup
        observation = read_json(folder(date) / "close-observation.json")
        observed = parse_time((observation or {}).get("observed_at"))
        ev = session.get("eval_date")
        if observation and ev and observation.get("eval_date") == ev and observed and observed >= parse_time(f"{ev}T15:00:00+08:00"):
            closing = followup.evaluate(pred, observation, final=True, pending=True)
    selected = None
    if at == "final":
        selected = final or closing
    elif at:
        if at not in times:
            raise ValueError("该时点没有已记录快照")
        selected = read_json(history[at])
    else:
        latest = read_json(folder(date) / "latest.json")
        legacy = read_json(paths[-1]) if paths else None
        candidates = [s for s in (latest, legacy) if s and parse_time(s.get("observed_at"))]
        selected = final or closing or max(candidates, key=lambda s: parse_time(s["observed_at"]), default=None)
    state = read_json(folder(date) / "status.json") or {}
    ev = session.get("eval_date")
    hhmm = now.strftime("%H:%M")
    auction = ev == now.date().isoformat() and "09:25" <= hhmm < "09:30"
    live = ev == now.date().isoformat() and (auction or "09:30" <= hhmm <= "11:30" or "13:00" <= hhmm <= "15:00")
    last = parse_time(state.get("last_success_at"))
    delayed = bool(live and (last is None or (now - last).total_seconds() > 180))
    eligible = bool(pred and ev and parse_time(pred["recorded_at"]) < parse_time(cutoff(ev)))
    return {"available": pred is not None, "prediction_date": date, "eval_date": ev,
            "prediction": with_binding_suggestions(pred), "cutoff_at": session.get("cutoff_at"), "editable": editable(date, now),
            "eligible": eligible, "times": times, "snapshot": selected, "final": final,
            "status": {**state, "delayed": delayed, "enabled": os.getenv("VIBE_FOLLOWUP_ENABLED", "1") != "0",
                       "phase": "已定稿" if final else "收盘待补齐" if closing else "交易日待确认" if not ev else
                       "等待开盘" if ev > now.date().isoformat() or (ev == now.date().isoformat() and hhmm < "09:30") else
                       "午休" if ev == now.date().isoformat() and "11:30" < hhmm < "13:00" else
                       "竞价验证" if auction else "盘中" if live else "等待收盘数据"},
            "notes": (read_json(folder(date) / "notes.json") or {}).get("items", [])}
