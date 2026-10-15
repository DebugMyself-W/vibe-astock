from __future__ import annotations

import queue
import json
import threading
import time

import pytest

from duanxian import anomaly_center


def test_start_worker_never_runs_gap_check_on_startup_thread(monkeypatch):
    anomaly_center.stop_worker()
    if anomaly_center._THREAD and anomaly_center._THREAD.is_alive():
        anomaly_center._THREAD.join(timeout=1)
    anomaly_center._THREAD = None
    while True:
        try:
            anomaly_center._QUEUE.get_nowait()
        except queue.Empty:
            break

    entered = threading.Event()
    release = threading.Event()

    def blocked_gap_check():
        entered.set()
        release.wait(timeout=1)
        return None

    monkeypatch.setattr(anomaly_center, "_missing_baseline_date", blocked_gap_check)
    started = time.monotonic()
    try:
        anomaly_center.start_worker()
        elapsed = time.monotonic() - started
        assert elapsed < 0.1, "启动钩子不能等待网络或数据库缺口检查"
        assert entered.wait(timeout=0.5), "缺口检查仍应由后台线程自动执行"
    finally:
        release.set()
        anomaly_center.stop_worker()
        if anomaly_center._THREAD:
            anomaly_center._THREAD.join(timeout=1)
        anomaly_center._THREAD = None


def test_market_profiles_cover_five_boards_and_st():
    assert anomaly_center.market_profile("600000")["benchmark"] == "SH000002"
    assert anomaly_center.market_profile("688001")["general_threshold"] == 30
    assert anomaly_center.market_profile("000001")["benchmark"] == "SZ399107"
    assert anomaly_center.market_profile("300001")["benchmark"] == "SZ399102"
    assert anomaly_center.market_profile("920001")["market"] == "BJ"
    assert anomaly_center.market_profile("000001", True)["general_threshold"] == 12
    assert anomaly_center._prediction_eligible({"name": "正常股票"}) is True
    assert anomaly_center._prediction_eligible({"name": "元道退"}) is False


def test_compounded_deviation_and_required_return_do_not_sum_daily_percentages():
    # 100 -> 110 -> 121 is +21%, not +20%.
    assert anomaly_center.compounded_return([100, 110, 121]) == pytest.approx(21)
    assert anomaly_center.cumulative_deviation([100, 110, 121], [100, 100, 100]) == pytest.approx(21)
    assert anomaly_center.required_return(1.5, 1.1, 100) == pytest.approx(40)


def test_factor_compounds_daily_returns_instead_of_mixing_adjusted_and_raw_closes():
    rows = [
        {"trade_date": "2026-10-09", "close": 3.42, "pct_chg": 2.0, "trade_status": 1},
        {"trade_date": "2026-10-12", "close": 14.85, "pct_chg": 1.0, "trade_status": 1},
        {"trade_date": "2026-10-13", "close": 14.82, "pct_chg": -0.2, "trade_status": 1},
        {"trade_date": "2026-10-14", "close": 14.14, "pct_chg": -4.59, "trade_status": 1},
    ]

    expected = 1.01 * 0.998 * 0.9541
    assert anomaly_center._factor(rows, 3) == pytest.approx(expected)


def test_established_stock_over_limit_row_is_not_misclassified_as_ipo_day():
    stock_rows = []
    benchmark_rows = []
    for index in range(35):
        day = f"2026-09-{index + 1:02d}" if index < 9 else f"2026-10-{index - 8:02d}"
        stock_rows.append({
            "trade_date": day, "open": 10, "high": 11, "low": 9, "close": 10,
            "pct_chg": 0.0 if index < 34 else 10.22, "trade_status": 1, "source": "fixture",
        })
        benchmark_rows.append({"trade_date": day, "close": 100, "pct_chg": 0.0, "source": "fixture"})

    candidate = anomaly_center._candidate(
        "600252",
        {"name": "中恒集团", "industry": "医药", "is_st": 0, "first_trade_date": "2000-11-09"},
        stock_rows,
        {"SH000002": benchmark_rows},
    )

    assert candidate is not None
    assert candidate["quote_time"] == stock_rows[-1]["trade_date"]


def test_prediction_groups_are_unique_and_only_include_one_day_reachable_rules():
    item = {
        "code": "920001", "name": "北证样本", "market": "BJ", "board": "北交所",
        "industry": "测试", "price": 10.0, "current_pct": 0.0, "quote_time": "2026-10-14",
        "benchmark": "BJ899050", "daily_limit": 30.0,
        "rules": [
            {"window_days": 10, "threshold": 100.0, "direction": "up", "rule": "近规则",
             "deviation": 80.0, "required_today": 10.0, "required_next": 12.0, "triggered": False},
            {"window_days": 30, "threshold": 200.0, "direction": "up", "rule": "重复规则",
             "deviation": 150.0, "required_today": 15.0, "required_next": 18.0, "triggered": False},
            {"window_days": 10, "threshold": -50.0, "direction": "down", "rule": "不可达规则",
             "deviation": -10.0, "required_today": -52.0, "required_next": -52.0, "triggered": False},
        ],
    }

    groups = anomaly_center._prediction_groups([item])

    assert [row["code"] for row in groups["next_day"]] == ["920001"]
    assert groups["next_day"][0]["rule"] == "近规则"
    assert groups["same_day"] == []


def test_same_day_predictions_open_at_0925_from_observed_quotes(monkeypatch):
    from datetime import datetime
    import vr.astock as astock

    payload = {
        "available": True, "coverage": "full", "trade_date": "2026-10-13",
        "updated_at": "2026-10-13T15:10:00+08:00",
        "groups": {
            "next_day": [{
                "code": "600000", "name": "浦发银行", "market": "SH", "board": "沪市主板",
                "industry": "银行", "price": 10.0, "current_pct": 0.0, "window_days": 10,
                "deviation": 90.0, "required_pct": 8.0, "status": "次日预测",
                "rule": "10日累计上涨100%", "quote_time": "2026-10-13",
                "benchmark": "SH000002", "daily_limit": 10.0, "direction": "up", "threshold": 100.0,
            }],
            "same_day": [], "triggered": [],
        },
        "quality": {"realtime_covered": 0},
    }
    monkeypatch.setattr(anomaly_center, "china_now", lambda: datetime(2026, 10, 14, 9, 25))
    monkeypatch.setattr(astock, "tencent_quote", lambda codes: {
        "600000": {"price": 10.5, "change_pct": 5.0, "quote_time": "2026-10-14T09:25:00+08:00"}
    })

    actual = anomaly_center._overlay_live_quotes(payload)

    assert [row["code"] for row in actual["groups"]["same_day"]] == ["600000"]
    assert actual["groups"]["same_day"][0]["current_pct"] == pytest.approx(5.0)
    assert actual["quality"]["realtime_covered"] == 1


def test_live_overlay_does_not_reclassify_public_source_groups(monkeypatch):
    from datetime import datetime

    payload = {
        "groups": {
            "next_day": [{"code": "600241"}],
            "same_day": [{"code": "002242"}, {"code": "301190"}],
            "triggered": [],
        },
        "quality": {"prediction_source": "eastmoney_price_anomaly"},
    }
    monkeypatch.setattr(anomaly_center, "china_now", lambda: datetime(2026, 10, 14, 10, 0))

    assert anomaly_center._overlay_live_quotes(payload) is payload
    assert [row["code"] for row in payload["groups"]["same_day"]] == ["002242", "301190"]


def test_anomaly_source_refusal_is_not_empty(monkeypatch):
    import vr.astock as astock

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"result": 403, "message": "denied", "data": []}

    monkeypatch.setattr(astock, "em_get", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="拒绝"):
        astock.em_price_anomaly()


def test_anomaly_fields_follow_eastmoney_contract(monkeypatch):
    import vr.astock as astock

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"result": 0, "date": "20261009", "pages": 1, "data": [{
                "c": "688137", "n": "近岸蛋白", "m": 1, "a": 8.32,
                "x": 103.18, "d": 10, "s": 6, "e": 4, "o": 1, "t": 6.88,
            }]}

    monkeypatch.setattr(astock, "em_get", lambda *args, **kwargs: Response())
    item = astock.em_price_anomaly()["items"][0]
    assert item["deviation"] == pytest.approx(103.18)
    assert item["days"] == 10
    assert item["board"] == 6
    assert item["rule_code"] == 40
    assert item["occurrence"] == 1
    assert item["target_pct"] == pytest.approx(6.88)
    assert item["event_date"] == "2026-10-09"


def test_official_prediction_groups_preserve_source_tri_state_and_targets():
    items = [
        {"code": "002242", "name": "九阳股份", "market": "SZ", "change_pct": 0.11,
         "deviation": 96.04, "days": 8, "rule_code": 4, "rule": "10日累计上涨100%",
         "occurrence": 2, "target_pct": 2.10, "event_date": "2026-10-14"},
        {"code": "600241", "name": "时代万恒", "market": "SH", "change_pct": 2.72,
         "deviation": 85.02, "days": 9, "rule_code": 4, "rule": "10日累计上涨100%",
         "occurrence": 2, "target_pct": 8.29, "event_date": "2026-10-14"},
        {"code": "002242", "name": "九阳股份", "market": "SZ", "change_pct": 0.11,
         "deviation": 96.04, "days": 8, "rule_code": 4, "rule": "10日累计上涨100%",
         "occurrence": 0, "target_pct": 2.22, "event_date": "2026-10-14"},
        {"code": "301190", "name": "善水科技", "market": "SZ", "change_pct": -6.87,
         "deviation": 8.61, "days": 3, "rule_code": 2, "rule": "创业板10日内3次同向异动",
         "occurrence": 0, "target_pct": 12.26, "event_date": "2026-10-14"},
        {"code": "301513", "name": "尚水智能", "market": "SZ", "change_pct": -8.89,
         "deviation": 53.75, "days": 7, "rule_code": 4, "rule": "10日累计上涨100%",
         "occurrence": 0, "target_pct": 19.77, "event_date": "2026-10-14"},
    ]

    groups = anomaly_center._official_prediction_groups(items)

    assert [row["code"] for row in groups["next_day"]] == ["002242", "600241"]
    assert [row["code"] for row in groups["same_day"]] == ["002242", "301190", "301513"]
    assert groups["triggered"] == []
    assert groups["same_day"][0]["required_pct"] == pytest.approx(2.22)


def test_monitors_only_publish_triggered_anomaly_rows(monkeypatch):
    import vr.astock as astock

    monkeypatch.setattr(astock, "em_stock_monitor", lambda only_active=False: [])
    monkeypatch.setattr(astock, "em_price_anomaly", lambda: {"items": [
        {"code": "002242", "name": "九阳股份", "market": "SZ", "occurrence": 0,
         "event_date": "2026-10-14", "rule": "预测"},
        {"code": "600241", "name": "时代万恒", "market": "SH", "occurrence": 1,
         "event_date": "2026-10-14", "rule": "已触发"},
        {"code": "301190", "name": "善水科技", "market": "SZ", "occurrence": 2,
         "event_date": "2026-10-14", "rule": "次日预测"},
    ]})

    actual = anomaly_center.monitors()

    assert actual["counts"]["severe_abnormal"] == 1
    assert actual["items"][0]["code"] == "600241"


def test_monitor_market_b_maps_to_bse(monkeypatch):
    import vr.astock as astock

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"STKCODE": "920001", "STKNAME": "北证样本", "MARKET": "B", "VALIDATESTARTDATE": "2026-01-01", "VALIDATEENDDATE": "2099-01-01"}]

    monkeypatch.setattr(astock, "em_get", lambda *args, **kwargs: Response())
    assert astock.em_stock_monitor()[0]["market"] == "BJ"


def test_partial_prediction_reports_missing_instead_of_publishing_empty(tmp_path, monkeypatch):
    path = tmp_path / "growth.sqlite"
    monkeypatch.setenv("GROWTH_LADDERS_DB", str(path))
    db = anomaly_center._connect()
    try:
        db.execute(
            "INSERT INTO session_instruments(trade_date,code,name,market,industry,trade_status,is_st) VALUES(?,?,?,?,?,?,?)",
            ("2026-10-09", "600825", "新华传媒", "SH", "文化传媒", 1, 0),
        )
        db.commit()
    finally:
        db.close()
    payload = anomaly_center.compute_predictions("2026-10-09")
    assert payload["coverage"] == "unavailable"
    assert payload["quality"]["missing_count"] == 1
    assert payload["groups"] == {"next_day": [], "same_day": [], "triggered": []}


def test_default_predictions_fall_back_to_latest_saved_baseline_when_calendar_is_ahead(tmp_path, monkeypatch):
    """A provisional calendar date must not hide the latest usable prediction baseline."""
    path = tmp_path / "growth.sqlite"
    monkeypatch.setenv("GROWTH_LADDERS_DB", str(path))
    monkeypatch.setattr(anomaly_center.trade_calendar, "latest_session", lambda: "2026-10-12")
    expected = {
        "available": True, "coverage": "partial", "trade_date": "2026-10-09",
        "updated_at": "2026-10-09T15:10:00+08:00", "algorithm_version": anomaly_center.ALGORITHM_VERSION,
        "groups": {"next_day": [{"code": "600825"}], "same_day": [], "triggered": []},
        "quality": {"universe_count": 5687, "history_covered": 5549, "announcement_covered": 20,
                    "realtime_covered": 0, "missing_count": 138, "source_hits": {}},
        "note": "fixture",
    }
    with anomaly_center._connect() as db:
        db.execute(
            "INSERT INTO anomaly_baselines(trade_date,algorithm_version,payload_json,universe_count,covered_count,generated_at) "
            "VALUES(?,?,?,?,?,?)",
            ("2026-10-09", anomaly_center.ALGORITHM_VERSION, json.dumps(expected), 5687, 5549, expected["updated_at"]),
        )
        db.commit()

    actual = anomaly_center.predictions()

    assert actual["available"] is True
    assert actual["trade_date"] == "2026-10-09"
    assert actual["groups"]["next_day"] == [{"code": "600825"}]


def test_missing_baseline_rebuilds_latest_materialized_corpus_when_saved_payload_is_stale(tmp_path, monkeypatch):
    path = tmp_path / "growth.sqlite"
    monkeypatch.setenv("GROWTH_LADDERS_DB", str(path))
    monkeypatch.setattr(anomaly_center.trade_calendar, "latest_session", lambda: "2026-10-14")
    stale = {
        "available": True, "coverage": "partial", "trade_date": "2026-10-13",
        "updated_at": "2026-10-13T17:39:23+08:00", "algorithm_version": anomaly_center.ALGORITHM_VERSION,
        "groups": {"next_day": [], "same_day": [], "triggered": []},
        "quality": {"universe_count": 5687, "history_covered": 2}, "note": "stale",
    }
    with anomaly_center._connect() as db:
        db.execute(
            "INSERT INTO session_instruments(trade_date,code,name,market,industry,trade_status,is_st) VALUES(?,?,?,?,?,?,?)",
            ("2026-10-13", "600825", "新华传媒", "SH", "文化传媒", 1, 0),
        )
        db.execute(
            "INSERT INTO corpus_date_coverage VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-10-13", 1, 1, 0, 0, 0, 0, 0, '{}',
             anomaly_center.growth_ladders.ALGORITHM_VERSION, "2026-10-14T08:45:04+08:00"),
        )
        db.execute(
            "INSERT INTO anomaly_baselines(trade_date,algorithm_version,payload_json,universe_count,covered_count,generated_at) "
            "VALUES(?,?,?,?,?,?)",
            ("2026-10-13", anomaly_center.ALGORITHM_VERSION, json.dumps(stale), 5687, 2, stale["updated_at"]),
        )
        db.commit()

    assert anomaly_center._missing_baseline_date() == "2026-10-13"

    fresh = {
        **stale, "coverage": "partial", "updated_at": "2026-10-14T09:00:00+08:00",
        "quality": {"universe_count": 1, "history_covered": 1}, "note": None,
    }
    calls = []
    monkeypatch.setattr(
        anomaly_center, "compute_predictions",
        lambda day: calls.append(day) or fresh,
    )

    actual = anomaly_center.predictions()

    assert calls == ["2026-10-13"]
    assert actual["quality"]["history_covered"] == 1
    assert "暂展示最近完整基线 2026-10-13" in actual["note"]


def test_baseline_staleness_uses_the_same_rule_eligible_universe(tmp_path, monkeypatch):
    path = tmp_path / "growth.sqlite"
    monkeypatch.setenv("GROWTH_LADDERS_DB", str(path))
    with anomaly_center._connect() as db:
        db.executemany(
            "INSERT INTO session_instruments(trade_date,code,name,market,industry,trade_status,is_st) VALUES(?,?,?,?,?,?,?)",
            [
                ("2026-10-14", "600000", "正常股票", "SH", "银行", 1, 0),
                ("2026-10-14", "000001", "元道退", "SZ", "软件", 1, 0),
            ],
        )
        db.execute(
            "INSERT INTO corpus_date_coverage VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-10-14", 2, 2, 0, 0, 0, 0, 0, '{}',
             anomaly_center.growth_ladders.ALGORITHM_VERSION, "2026-10-14T18:00:00+08:00"),
        )
        baseline = {"universe_count": 1, "generated_at": "2026-10-14T19:00:00+08:00"}
        assert anomaly_center._baseline_is_stale(db, "2026-10-14", baseline) is False


def _route(path: str, method: str = "GET"):
    import server

    return next(
        route for route in server.app.router.routes
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set())
    ).endpoint


def test_prediction_api_contract(monkeypatch):
    expected = {"available": True, "coverage": "partial", "groups": {"next_day": [], "same_day": [], "triggered": []}}
    monkeypatch.setattr(anomaly_center, "predictions", lambda date=None, refresh_official=False: expected)
    assert _route("/api/market/anomaly-center/predictions")(None)["data"] == expected


def test_monitor_api_filter_and_pagination(monkeypatch):
    expected = {"items": [], "counts": {"all": 0, "risk_warning": 0, "severe_abnormal": 0}}
    monkeypatch.setattr(anomaly_center, "monitors", lambda kind, page, page_size: expected)
    assert _route("/api/market/anomaly-center/monitors")("risk_warning", 2, 20)["data"] == expected


def test_sync_api_is_idempotent(monkeypatch):
    expected = {"state": "running", "phase": "同步基准指数", "progress": 40}
    monkeypatch.setattr(anomaly_center, "request_sync", lambda date=None: expected)
    endpoint = _route("/api/market/anomaly-center/sync", "POST")
    assert endpoint("2026-10-09")["data"] == endpoint("2026-10-09")["data"] == expected


def test_benchmark_falls_back_when_eastmoney_is_unavailable(monkeypatch):
    fixture = [{"symbol": "SH000002", "trade_date": "2026-10-09", "close": 4321.0,
                "pct_chg": 0.8, "source": "tencent_qfq"}]
    monkeypatch.setattr(anomaly_center, "_fetch_benchmark_tencent", lambda *args: fixture)
    monkeypatch.setattr(anomaly_center, "_fetch_benchmark_sina", lambda *args: [])
    monkeypatch.setattr(anomaly_center, "_fetch_benchmark_eastmoney", lambda *args: (_ for _ in ()).throw(RuntimeError("proxy down")))

    assert anomaly_center._fetch_benchmark("SH000002", "2026-01-01", "2026-10-09") == fixture


def test_bse_benchmark_uses_sina_when_tencent_has_no_index(monkeypatch):
    fixture = [{"symbol": "BJ899050", "trade_date": "2026-10-09", "close": 1500.0,
                "pct_chg": 1.2, "source": "sina_raw"}]
    monkeypatch.setattr(anomaly_center, "_fetch_benchmark_tencent", lambda *args: [])
    monkeypatch.setattr(anomaly_center, "_fetch_benchmark_sina", lambda *args: fixture)
    monkeypatch.setattr(anomaly_center, "_fetch_benchmark_eastmoney", lambda *args: [])

    assert anomaly_center._fetch_benchmark("BJ899050", "2026-01-01", "2026-10-09") == fixture
