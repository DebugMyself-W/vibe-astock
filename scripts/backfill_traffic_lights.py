"""One-shot backfill for explicitly requested historical review dates.

Only dates supplied on the command line are touched.  Existing nested lights
are preserved; reconstructed/missing data is left as “未记录” rather than
borrowing another session.
"""
from __future__ import annotations
import json, os, sys
from duanxian import board_traffic_light, market_facts, review_store

def main(days):
    for day in days:
        path = os.path.join(review_store.DIR, f"{day}.json")
        payload = review_store._read(path)
        if not isinstance(payload, dict):
            print(f"{day}: missing"); continue
        if isinstance(payload.get("board_traffic_light"), dict):
            env = payload["board_traffic_light"]
        else:
            env = board_traffic_light.evaluate_settled(payload)
            payload["board_traffic_light"] = env
        payload = board_traffic_light.attach_feedback_stock_lights(payload, pool=market_facts.pools(day))
        review_store._atomic_write(path, payload)
        print(f"{day}: updated")

if __name__ == "__main__":
    main(sys.argv[1:] or ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27"])
