"""Backfill date-isolated close traffic lights into historical review snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from duanxian import market_facts, review_store
from duanxian.board_traffic_light import attach_feedback_stock_lights


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _details(review: dict) -> list[dict]:
    return review["market_facts"]["feedback_matrix"]["details"]


def _prepare(date: str) -> tuple[dict, dict]:
    review = review_store.load(date)
    if not isinstance(review, dict):
        raise RuntimeError(f"{date}: review snapshot is missing")
    for key in ("target_date", "trade_date"):
        value = review.get(key)
        if value and value != date:
            raise RuntimeError(f"{date}: {key} is {value!r}")
    if review.get("target_date") != date:
        raise RuntimeError(f"{date}: target_date is not exact")

    before = _details(review)
    if not before:
        raise RuntimeError(f"{date}: feedback details are empty")

    pool = market_facts.pools(date)
    if not isinstance(pool, dict) or not pool.get("zt"):
        raise RuntimeError(f"{date}: exact-date close limit-up pool is unavailable")

    cache_path = Path(market_facts._CACHE_DIR) / f"{date}.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    if cache.get("date") != date or cache.get("pools") != pool:
        raise RuntimeError(f"{date}: market-facts cache provenance mismatch")

    enriched = attach_feedback_stock_lights(review, pool=pool)
    after = _details(enriched)
    if len(after) != len(before):
        raise RuntimeError(f"{date}: feedback detail count changed")
    if Counter(str(x.get("code") or "") for x in after) != Counter(
        str(x.get("code") or "") for x in before
    ):
        raise RuntimeError(f"{date}: feedback stock universe changed")

    zt_codes = {str(row.get("code") or "").zfill(6) for row in pool["zt"]}
    labels: Counter[str] = Counter()
    for detail in after:
        code = str(detail.get("code") or "").zfill(6)
        light = detail.get("traffic_light")
        if not isinstance(light, dict):
            raise RuntimeError(f"{date}/{code}: close light is missing")
        if light.get("date") != date or light.get("as_of") != "15:00:00":
            raise RuntimeError(f"{date}/{code}: light timestamp mismatch")
        env_date = (light.get("environment") or {}).get("date")
        if env_date not in (None, date):
            raise RuntimeError(f"{date}/{code}: environment date is {env_date!r}")
        label = str((light.get("quality") or {}).get("label") or "")
        labels[label] += 1
        if code in zt_codes:
            if light.get("stage") != "sealed" or label == "当日未封板":
                raise RuntimeError(f"{date}/{code}: exact pool says sealed but light does not")
        elif light.get("stage") != "not_ready" or label != "当日未封板":
            raise RuntimeError(f"{date}/{code}: stock is absent from exact close pool")

    audit = {
        "date": date,
        "details": len(after),
        "pool_zt": len(pool["zt"]),
        "pool_zb": len(pool.get("zb") or []),
        "pool_dt": len(pool.get("dt") or []),
        "labels": dict(sorted(labels.items())),
        "all_as_of": "15:00:00",
    }
    return enriched, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dates", nargs="+")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    review_dir = Path(os.path.expanduser(review_store.DIR))
    latest_path = review_dir / "latest.json"
    latest_before = _sha256(latest_path)

    prepared: dict[str, dict] = {}
    audits: list[dict] = []
    for date in args.dates:
        prepared[date], audit = _prepare(date)
        audits.append(audit)

    if args.write:
        for date, enriched in prepared.items():
            dated_path = review_dir / f"{date}.json"
            backup_path = review_dir / f"{date}.pre-stock-lights.json"
            if not backup_path.exists():
                shutil.copy2(dated_path, backup_path)
            review_store._atomic_write(str(dated_path), enriched)

        for date in args.dates:
            stored, _ = _prepare(date)
            if _details(stored) != _details(prepared[date]):
                raise RuntimeError(f"{date}: persisted traffic lights changed after reload")

    if _sha256(latest_path) != latest_before:
        raise RuntimeError("latest.json changed during historical backfill")

    print(json.dumps({"mode": "write" if args.write else "dry-run", "audits": audits},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
