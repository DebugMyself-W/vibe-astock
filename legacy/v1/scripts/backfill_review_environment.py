"""Freeze missing historical index closes and refresh dependent close lights."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from duanxian import market_facts, review_store
from duanxian.board_traffic_light import attach_feedback_stock_lights
from duanxian.fetchers import A_INDICES, fetch_a_index_day


def _details(review: dict) -> list[dict]:
    return ((((review.get("market_facts") or {}).get("feedback_matrix") or {}).get("details")) or [])


def _prepare(date: str) -> tuple[dict, dict]:
    path = Path(os.path.expanduser(review_store.DIR)) / f"{date}.json"
    raw = review_store._read(str(path))
    if not isinstance(raw, dict) or raw.get("target_date") != date:
        raise RuntimeError(f"{date}: review snapshot/date mismatch")
    if raw.get("trade_date") not in (None, date):
        raise RuntimeError(f"{date}: trade_date mismatch")

    facts = raw.get("market_facts")
    if not isinstance(facts, dict):
        raise RuntimeError(f"{date}: market facts are missing")
    indices = facts.get("indices")
    if not (isinstance(indices, dict) and indices.get("available") is True
            and indices.get("date") == date and len(indices.get("items") or []) == len(A_INDICES)):
        indices = fetch_a_index_day(date)
    expected_codes = [code for code, _ in A_INDICES]
    actual_codes = [str(item.get("code") or "") for item in indices.get("items") or []]
    if (indices.get("available") is not True or indices.get("date") != date
            or actual_codes != expected_codes):
        raise RuntimeError(f"{date}: exact-date four-index close is incomplete: {indices!r}")

    enriched = {**raw, "market_facts": {**facts, "indices": indices}}
    enriched = review_store._with_regime(enriched)
    enriched = review_store._with_entry(enriched)
    enriched = review_store._with_traffic_light(enriched)
    pool = market_facts.pools(date)
    if not isinstance(pool, dict) or not pool.get("zt"):
        raise RuntimeError(f"{date}: exact-date close pool is unavailable")
    enriched = attach_feedback_stock_lights(enriched, pool=pool)

    old_quality = {str(row.get("code") or ""): (row.get("traffic_light") or {}).get("quality")
                   for row in _details(raw) if isinstance(row, dict) and row.get("traffic_light")}
    for row in _details(enriched):
        code = str(row.get("code") or "")
        light = row.get("traffic_light") or {}
        if light.get("date") != date or light.get("as_of") != "15:00:00":
            raise RuntimeError(f"{date}/{code}: refreshed light timestamp mismatch")
        if code in old_quality and light.get("quality") != old_quality[code]:
            raise RuntimeError(f"{date}/{code}: quality changed while only environment was backfilled")
    traffic = enriched.get("board_traffic_light") or {}
    if "market:indices" in (traffic.get("missing") or []):
        raise RuntimeError(f"{date}: indices remain missing after backfill")
    audit = {
        "date": date, "indices": len(indices["items"]),
        "environment": traffic.get("light"), "environment_label": traffic.get("label"),
        "remaining_missing": traffic.get("missing") or [],
        "entry_missing": (enriched.get("entry_decision") or {}).get("missing") or [],
        "entry_issues": (enriched.get("entry_decision") or {}).get("issues") or [],
        "warnings": raw.get("warnings") or [],
        "stock_lights": len(_details(enriched)),
        "pending_actions": sum((row.get("traffic_light") or {}).get("action", {}).get("light") == "gray"
                               for row in _details(enriched)),
    }
    return enriched, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dates", nargs="+")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    review_dir = Path(os.path.expanduser(review_store.DIR))

    prepared, audits = {}, []
    for date in args.dates:
        prepared[date], audit = _prepare(date)
        audits.append(audit)

    if args.write:
        latest_path = review_dir / "latest.json"
        latest = review_store._read(str(latest_path))
        latest_date = (latest or {}).get("target_date")
        for date, enriched in prepared.items():
            dated_path = review_dir / f"{date}.json"
            backup = review_dir / f"{date}.pre-environment-backfill.json"
            if not backup.exists():
                shutil.copy2(dated_path, backup)
            review_store._atomic_write(str(dated_path), enriched)
            if latest_date == date:
                latest_backup = review_dir / "latest.pre-environment-backfill.json"
                if not latest_backup.exists():
                    shutil.copy2(latest_path, latest_backup)
                review_store._atomic_write(str(latest_path), enriched)

    print(json.dumps({"mode": "write" if args.write else "dry-run", "audits": audits},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
