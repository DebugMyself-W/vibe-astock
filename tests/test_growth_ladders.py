from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


def _bar(code, day, open_, close, pct=None, *, status=1, source="fixture", is_st=0):
    return {
        "code": code, "trade_date": day, "open": open_, "close": close,
        "high": max(open_ or 0, close or 0), "low": min(open_ or 0, close or 0),
        "pct_chg": pct, "volume": 1, "amount": 1, "trade_status": status,
        "is_st": is_st, "source": source, "fetched_at": "2026-10-09T15:10:00+08:00",
    }


def _instrument(code, name=None, *, status=1, is_st=0):
    return {
        "code": code, "name": name or code, "market": "SH" if code.startswith("6") else "SZ",
        "industry": "测试行业", "industry_source": "fixture", "trade_status": status, "is_st": is_st,
    }


@pytest.fixture
def growth(tmp_path, monkeypatch):
    monkeypatch.setenv("GROWTH_LADDERS_DB", str(tmp_path / "growth.sqlite"))
    from duanxian import growth_ladders

    growth_ladders.reset_for_tests()
    return growth_ladders


def test_streak_reaches_twelve_and_counts_non_falling_doji(growth):
    days = [f"2026-09-{day:02d}" for day in range(16, 28)]
    rows = []
    for index, day in enumerate(days):
        open_ = 10 + index * 0.1
        close = open_ if index == 5 else open_ + 0.05
        rows.append(_bar("600001", day, open_, close, 0.0 if index == 5 else 0.5))

    result = growth.streak_at(rows, days[-1])

    assert result["positive_level"] == 12
    assert result["rising_level"] == 6
    assert result["level"] == 12


def test_streak_uses_larger_of_rise_and_positive_candle(growth):
    rows = [
        _bar("600001", "2026-10-01", 10.0, 10.1, 1.0),
        _bar("600001", "2026-10-02", 10.2, 10.1, 0.2),
        _bar("600001", "2026-10-03", 10.3, 10.2, 0.3),
    ]

    result = growth.streak_at(rows, "2026-10-03")

    assert result["positive_level"] == 0
    assert result["rising_level"] == 3
    assert result["level"] == 3


def test_ex_right_price_gap_does_not_create_false_decline(growth):
    rows = [
        _bar("600001", "2026-10-01", 100, 101, 1.0),
        _bar("600001", "2026-10-02", 50, 51, 0.8),
        _bar("600001", "2026-10-03", 51, 52, 1.2),
    ]

    result = growth.streak_at(rows, "2026-10-03")

    assert result["rising_level"] == 3
    assert result["pct_chg"] == 1.2
    assert result["stage_high"] is True


def test_compute_snapshot_has_twelve_day_stock_and_only_valid_breaks(growth):
    current, previous = "2026-10-09", "2026-10-08"
    active, broken, suspended, short = "600001", "600002", "600003", "600004"
    instruments = [_instrument(active, "十二天标的"), _instrument(broken, "断阳标的"),
                   _instrument(suspended, "停牌标的"), _instrument(short, "四天标的")]
    growth._store_instruments(previous, instruments)
    growth._store_instruments(current, instruments)
    bars = []
    days = ["2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23",
            "2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29", previous, current]
    for index, day in enumerate(days):
        bars.append(_bar(active, day, 10 + index, 10.5 + index, 0.5))
    for code, count, final_status in ((broken, 6, 1), (suspended, 6, 0), (short, 4, 1)):
        for index, day in enumerate(days[-(count + 1):-1]):
            bars.append(_bar(code, day, 10 + index, 10.5 + index, 0.5))
        if final_status:
            bars.append(_bar(code, current, 20, 19, -2.0))
        else:
            bars.append(_bar(code, current, None, None, None, status=0))
    growth._store_bars(bars)

    result = growth.compute_snapshot(current, previous)

    by_code = {item["code"]: item for level in result["levels"] for item in level["items"]}
    assert by_code[active]["level"] == 12
    assert by_code[broken]["eliminated"] is True
    assert suspended not in by_code
    assert short not in by_code
    assert result["quality"]["suspended_count"] == 1
    assert result["coverage"] == "full"


def test_unknown_missing_symbol_blocks_publication(growth):
    day = "2026-10-09"
    growth._store_instruments(day, [_instrument("600001"), _instrument("600002")])
    growth._store_bars([_bar("600001", day, 10, 11, 10)])

    with pytest.raises(growth.CoverageError) as error:
        growth.compute_snapshot(day, None)

    assert error.value.missing == ["600002"]


def test_bj_gap_is_suspension_only_when_bracketed_by_real_sessions(growth):
    code = "920001"
    instrument = {**_instrument(code), "market": "BJ"}
    for day in ("2026-10-08", "2026-10-09"):
        growth._store_instruments(day, [instrument])
    growth._store_bars([
        _bar(code, "2026-10-07", 10, 10.2, 2),
        _bar(code, "2026-10-09", 10.2, 10.4, 2),
    ])

    growth._fill_proven_suspension_gaps(["2026-10-08", "2026-10-09"])

    histories = growth._load_histories([code], "2026-10-09")
    inferred = next(row for row in histories[code] if row["trade_date"] == "2026-10-08")
    assert inferred["trade_status"] == 0
    assert inferred["source"] == "inferred_suspension_between_trades"


def test_history_provider_exception_falls_through_to_next_source(growth, monkeypatch):
    expected = [_bar("600001", "2026-10-09", 10, 11, 10, source="ths_qfq")]
    monkeypatch.setattr(growth, "_baostock_history", lambda *_args: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(growth, "_ths_history", lambda *_args: expected)
    monkeypatch.setattr(growth, "_mootdx_history", lambda *_args: (_ for _ in ()).throw(AssertionError("unused")))

    rows = growth._dated_fallback_history(object(), "600001", "2026-10-09", "2026-10-09", 10)

    assert rows == expected


def test_tencent_legacy_bse_quote_is_marked_stale():
    from vr import astock

    values = [""] * 53
    values[1] = "旧北交所代码"
    values[3] = "12.34"
    values[4] = "12.34"
    values[5] = "12.34"
    values[30] = "20261013100000"
    raw = 'v_bj832317="' + "~".join(values) + '";'

    quote = astock._parse_gtimg(raw)["832317"]

    assert quote["is_stale"] is True
    assert quote["stale_reason"] == "legacy_bj_code_requires_canonical_920"


def test_snapshot_batch_is_atomic_and_algorithm_version_invalidates_old_data(growth, monkeypatch):
    payload = {
        "available": True, "date": "2026-10-09", "live": False, "updated_at": "now",
        "rule": "rule", "source": "fixture", "coverage": "full", "note": "",
        "available_dates": [], "levels": [],
        "quality": {"universe_count": 1, "covered_count": 1, "missing_count": 0,
                    "suspended_count": 0, "source_hits": {"fixture": 1},
                    "algorithm_version": growth.ALGORITHM_VERSION},
        "sync": {"state": "ready", "phase": "complete", "completed": 1, "total": 1, "percent": 100},
    }
    with pytest.raises(KeyError):
        growth._save_snapshots([payload, {"date": "2026-10-08"}])
    with growth._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ladder_snapshots").fetchone()[0] == 0

    growth._save_snapshot(payload)
    assert growth._ready_dates() == ["2026-10-09"]
    monkeypatch.setattr(growth, "ALGORITHM_VERSION", "growth-ladder-next")
    assert growth._ready_dates() == []


def test_bar_storage_is_idempotent(growth):
    row = {**_bar("600001", "2026-10-09", 10, 11, 10), "turnover_pct": 6.25}
    growth._store_bars([row, row])
    growth._store_bars([{**row, "close": 12, "pct_chg": 20, "turnover_pct": 7.5}])

    with growth._connect() as db:
        stored = db.execute("SELECT close,pct_chg,turnover_pct FROM daily_bars").fetchall()
    assert len(stored) == 1
    assert tuple(stored[0]) == (12.0, 20.0, 7.5)


def test_ensure_corpus_window_reuses_history_and_publishes_daily_coverage(growth, monkeypatch):
    dates = ["2026-10-07", "2026-10-08", "2026-10-09"]
    instrument = _instrument("600001", "语料标的")
    fetched = []
    progress = []

    monkeypatch.setattr(growth, "_full_market_universe", lambda **_kwargs: [instrument])
    monkeypatch.setattr(growth, "_open_baostock", lambda: None)
    monkeypatch.setattr(
        growth,
        "_session_universe",
        lambda _bs, _day, current: ({"600001": dict(current["600001"])}, "fixture_directory"),
    )

    def history(_bs, code, start, end, _limit):
        fetched.append((code, start, end))
        return [
            {**_bar(code, day, 10 + index, 10.5 + index, 5), "turnover_pct": 3 + index}
            for index, day in enumerate(dates)
        ]

    monkeypatch.setattr(growth, "_dated_fallback_history", history)

    result = growth.ensure_corpus_window(dates, lambda payload: progress.append(payload))

    assert fetched == [("600001", dates[0], dates[-1])]
    assert result["ready_dates"] == dates
    assert result["missing_dates"] == []
    assert result["source_hits"] == {"eastmoney_directory": 1, "fixture_directory": 3, "fixture": 1}
    assert {item["phase"] for item in progress} == {"directory_repair", "corpus_universe", "corpus_bars"}
    with growth._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM session_instruments").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0] == 3


def test_ensure_corpus_window_fetches_only_missing_bounding_interval(growth, monkeypatch):
    dates = ["2026-10-07", "2026-10-08", "2026-10-09"]
    instrument = _instrument("600001", "增量标的")
    for day in dates:
        growth._store_instruments(day, [instrument])
    growth._store_bars([{**_bar("600001", dates[-1], 10, 10.5, 5), "turnover_pct": 3}])
    fetched = []
    monkeypatch.setattr(growth, "_full_market_universe", lambda **_kwargs: [instrument])
    monkeypatch.setattr(growth, "_open_baostock", lambda: None)

    def history(_bs, code, start, end, _limit):
        fetched.append((code, start, end))
        return [_bar(code, day, 10, 10.5, 5) for day in dates[:2]]

    monkeypatch.setattr(growth, "_dated_fallback_history", history)

    result = growth.ensure_corpus_window(dates)

    assert fetched == [("600001", dates[0], dates[1])]
    assert result["ready_dates"] == dates


def test_ensure_corpus_window_batches_a_newly_settled_session(growth, monkeypatch):
    day = "2026-10-14"
    instruments = [_instrument("600001", "批量一"), _instrument("000001", "批量二")]
    directory = {item["code"]: item for item in instruments}
    calls = []

    monkeypatch.setattr(growth, "china_now", lambda: datetime(2026, 10, 14, 16, 0))
    monkeypatch.setattr(growth, "_full_market_universe", lambda **_kwargs: instruments)
    monkeypatch.setattr(growth, "_open_baostock", lambda: None)
    monkeypatch.setattr(
        growth,
        "_session_universe",
        lambda _bs, _day, _current: (directory, "fixture_directory"),
    )

    def tencent(codes, trade_date):
        calls.append((list(codes), trade_date))
        return ([_bar(code, day, 10, 10.5, 5) for code in codes], [])

    monkeypatch.setattr(growth, "_tencent_incremental", tencent)
    monkeypatch.setattr(
        growth,
        "_dated_fallback_history",
        lambda *_args, **_kwargs: pytest.fail("批量收盘行情完整时不应逐股回补"),
    )

    result = growth.ensure_corpus_window([day])

    assert calls == [(["000001", "600001"], day)]
    assert result["ready_dates"] == [day]
    assert result["source_hits"]["tencent_close"] == 2


def test_only_explicitly_inactive_symbols_are_trimmed_after_last_trade(growth):
    dates = ["2026-10-07", "2026-10-08", "2026-10-09"]
    inactive, unknown = _instrument("600001"), _instrument("600002")
    for day in dates:
        growth._store_instruments(day, [inactive, unknown])
    growth._store_bars([
        _bar("600001", dates[0], 10, 10.5, 5),
        _bar("600001", dates[1], 10.5, 11, 5),
        _bar("600002", dates[0], 10, 10.5, 5),
        _bar("600002", dates[1], 10.5, 11, 5),
    ])

    growth._drop_after_proven_delisting(["600001"], dates)

    assert {row["code"] for row in growth._load_instruments(dates[-1])} == {"600002"}


def test_sync_trigger_is_single_flight(growth, monkeypatch):
    monkeypatch.setattr(growth, "status", lambda: {"state": "building", "run_id": "existing"})
    monkeypatch.setattr(growth, "_new_run", lambda _day: (_ for _ in ()).throw(AssertionError("duplicate run")))
    started = []
    monkeypatch.setattr(growth, "start_worker", lambda: started.append(True))

    assert growth.request_sync("2026-10-09")["run_id"] == "existing"
    assert started == [True]
    assert growth._SYNC_QUEUE.get_nowait() == "2026-10-09"


def test_historical_universe_reuses_local_snapshot_when_baostock_is_down(growth, monkeypatch):
    day = "2026-10-09"
    growth._store_instruments(day, [_instrument("600001", "本地快照")])
    monkeypatch.setattr(
        growth,
        "_baostock_session_universe",
        lambda *_args: (_ for _ in ()).throw(AssertionError("本地快照存在时不应访问 Baostock")),
    )

    rows, source = growth._session_universe(None, day, {})

    assert rows["600001"]["name"] == "本地快照"
    assert source == "local_snapshot"


def test_baostock_directory_validates_exchange_prefix_before_code(growth):
    class Result:
        error_code = "0"
        fields = ["code", "code_name", "tradeStatus"]

        def __init__(self):
            self.rows = iter([
                ["sh.000300", "沪深300", "1"],
                ["sh.000041", "上证债券指数", "1"],
                ["sh.900901", "云赛B股", "1"],
                ["sz.000001", "平安银行", "1"],
                ["sh.600000", "浦发银行", "1"],
                ["sh.688001", "华兴源创", "1"],
            ])

        def next(self):
            try:
                self.current = next(self.rows)
                return True
            except StopIteration:
                return False

        def get_row_data(self):
            return self.current

    class BS:
        @staticmethod
        def query_all_stock(day):
            return Result()

    rows = growth._baostock_session_universe(BS(), "2026-10-09")

    assert set(rows) == {"000001", "600000", "688001"}
    assert rows["000001"]["market"] == "SZ"
    assert rows["600000"]["source_code"] == "sh.600000"


def test_directory_repair_replaces_polluted_snapshot_in_place(growth, monkeypatch):
    day = "2026-10-09"
    growth._store_instruments(day, [
        _instrument("000300", "沪深300"),
        _instrument("600000", "浦发银行"),
    ])
    growth._store_bars([_bar("600000", day, 10, 11, 10)])
    directory = [_instrument("600000", "浦发银行")]
    directory[0]["directory_active"] = 1
    monkeypatch.setattr(growth, "_full_market_universe", lambda **_kwargs: directory)
    monkeypatch.setattr(growth, "_open_baostock", lambda: object())
    monkeypatch.setattr(growth, "_baostock_session_universe", lambda *_args: {
        "600000": {**directory[0], "source_code": "sh.600000"},
    })

    result = growth.ensure_corpus_window([day])

    assert {row["code"] for row in growth._load_instruments(day)} == {"600000"}
    assert result["repair"][day]["invalid_removed"] == 1
    with growth._connect() as db:
        coverage = db.execute(
            "SELECT invalid_directory_count,unknown_count FROM corpus_date_coverage WHERE trade_date=?", (day,)
        ).fetchone()
    assert tuple(coverage) == (1, 0)


def test_directory_repair_purges_pollution_outside_requested_window(growth):
    growth._store_instruments("2025-01-02", [
        _instrument("000022", "沪公司债"),
        _instrument("000041", "上证公用事业行业指数"),
        _instrument("000001", "平安银行"),
    ])
    growth._store_bars([_bar("000022", "2025-01-02", 1, 1, 0)])

    removed = growth._purge_known_directory_pollution()

    assert removed == 2
    assert {row["code"] for row in growth._load_instruments("2025-01-02")} == {"000001"}
    with growth._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM daily_bars WHERE code='000022'").fetchone()[0] == 1


def test_corpus_retry_reuses_fresh_instrument_master(growth, monkeypatch):
    day = "2026-10-09"
    current = _instrument("600000", "浦发银行")
    current["directory_active"] = 1
    monkeypatch.setattr(growth, "china_now", lambda: datetime(2026, 10, 9, 16, tzinfo=timezone.utc))
    growth._refresh_instrument_master({"600000": current})
    growth._store_bars([_bar("600000", day, 10, 11, 10)])
    monkeypatch.setattr(
        growth, "_full_market_universe",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("fresh master must avoid a remote directory scan")),
    )
    monkeypatch.setattr(growth, "_open_baostock", lambda: None)

    result = growth.ensure_corpus_window([day])

    assert result["ready_dates"] == [day]
    assert result["source_hits"]["instrument_master_cache"] == 1


def test_canonical_snapshot_rejects_unnamed_index_and_untraded_bse_listing(growth):
    day = "2026-10-09"
    real = _instrument("600000", "浦发银行")
    future_bj = {**_instrument("920162", "莫森泰克"), "market": "BJ", "directory_active": 1}
    traded_bj = {**_instrument("920001", "北交所样本"), "market": "BJ", "directory_active": 1}
    growth._store_instruments(day, [
        real,
        _instrument("000044", "上证中盘"),
        future_bj,
        traded_bj,
    ])
    growth._store_bars([_bar("920001", "2026-10-08", 10, 11, 10)])
    current = {row["code"]: row for row in (real, future_bj, traded_bj)}

    rows, source = growth._session_universe(None, day, current)

    assert source == "local_snapshot"
    assert set(rows) == {"600000", "920001"}


def test_historical_universe_falls_back_to_current_directory(growth, monkeypatch):
    monkeypatch.setattr(
        growth,
        "_baostock_session_universe",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("10002007 网络接收错误")),
    )
    current = {"600001": _instrument("600001", "当前目录")}

    rows, source = growth._session_universe(object(), "2026-10-09", current)

    assert rows == current
    assert source == "current_directory_fallback"


def test_full_market_directory_rejects_cross_market_index_codes(growth, monkeypatch):
    from duanxian import fetchers

    monkeypatch.setattr(fetchers, "_clist", lambda *_args, **_kwargs: [
        {"f12": "000001", "f13": 1, "f14": "上证指数", "f100": "-"},
        {"f12": "000001", "f13": 0, "f14": "平安银行", "f100": "银行"},
        {"f12": "600000", "f13": 1, "f14": "浦发银行", "f100": "银行"},
        {"f2": "-", "f18": "-", "f12": "000003", "f13": 0, "f14": "PT金田A", "f100": "-"},
    ])

    rows = growth._full_market_universe()

    assert {(row["code"], row["name"]) for row in rows} == {
        ("000001", "平安银行"), ("600000", "浦发银行")
    }


def test_mature_corpus_filters_delisted_and_future_directory_entries(growth):
    growth._store_bars([
        _bar("000001", "2026-10-09", 10, 11, 10),
        _bar("600000", "2026-10-09", 10, 11, 10),
    ])
    directory = {
        "000001": _instrument("000001", "平安银行"),
        "600000": _instrument("600000", "浦发银行"),
        "000003": _instrument("000003", "PT金田A"),
    }

    filtered = growth._filter_universe_by_recent_activity(
        directory, "2026-09-01", minimum_verified=2,
    )

    assert set(filtered) == {"000001", "600000"}


def test_failed_worker_uses_five_minute_backoff_and_keeps_requested_date(growth, monkeypatch):
    growth._WORKER_STOP.clear()
    growth._SYNC_QUEUE.put_nowait("2026-10-09")
    def fail(_day):
        growth._WORKER_STOP.set()
        raise RuntimeError("offline")
    monkeypatch.setattr(growth, "_sync_once", fail)
    before = growth.time.monotonic()

    growth._worker_loop()

    assert growth._retry_count == 1
    assert growth._retry_requested == "2026-10-09"
    assert growth._retry_at >= before + 299
    growth._WORKER_STOP.clear()


def _route(server, path, method="GET"):
    return next(route for route in server.app.routes
                if getattr(route, "path", "") == path and method in getattr(route, "methods", set()))


def test_growth_ladder_route_uses_current_ladder_resolver(monkeypatch):
    import server
    from duanxian import growth_ladders

    route = _route(server, "/api/market/growth-ladders/yang")
    requested = []
    monkeypatch.setattr(growth_ladders, "get_current_ladder", lambda day=None: {
        "available": True, "date": day or "2026-10-12", "coverage": "full",
        "live": day is None, "levels": [],
    })

    client = TestClient(server.app, base_url="http://127.0.0.1")
    response = client.get("/api/market/growth-ladders/yang")
    historical = client.get("/api/market/growth-ladders/yang?date=2026-10-09")

    assert response.status_code == 200
    assert response.json()["data"]["date"] == "2026-10-12"
    assert response.json()["data"]["live"] is True
    assert historical.json()["data"]["date"] == "2026-10-09"


def test_current_session_is_computed_in_memory_and_not_persisted(growth, monkeypatch):
    from duanxian import trade_calendar

    previous, current = "2026-10-09", "2026-10-12"
    code = "600001"
    growth._store_instruments(previous, [_instrument(code, "盘中标的")])
    growth._store_bars([
        _bar(code, "2026-10-07", 10.0, 10.2, 2.0),
        _bar(code, "2026-10-08", 10.2, 10.4, 1.96),
        _bar(code, previous, 10.4, 10.6, 1.92),
    ])
    closed = growth.compute_snapshot(previous, None)
    growth._save_snapshot(closed)

    live_row = _bar(code, current, 10.6, 10.8, 1.89, source="tencent_live")
    live_row["name"] = "盘中标的"
    monkeypatch.setattr(growth, "_tencent_live_rows", lambda codes, day: ([live_row], []))
    monkeypatch.setattr(trade_calendar, "quote_trade_day", lambda: current)
    monkeypatch.setattr(trade_calendar, "session_phase", lambda *_args: {
        "phase_key": "open", "phase": "盘中", "label": "盘中", "poll": True,
    })
    monkeypatch.setattr(
        growth, "china_now",
        lambda: datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc),
    )

    result = growth.get_current_ladder()

    assert result["available"] is True
    assert result["date"] == current
    assert result["live"] is True
    assert result["levels"][0]["items"][0]["level"] == 4
    assert result["quality"]["source_hits"] == {"tencent_live": 1}
    assert current in result["available_dates"]
    with growth._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM daily_bars WHERE trade_date=?", (current,),
        ).fetchone()[0] == 0


def test_growth_ladder_route_rejects_bad_date():
    import server

    response = TestClient(server.app, base_url="http://127.0.0.1").get(
        "/api/market/growth-ladders/yang?date=2026-13-40"
    )
    assert response.status_code == 400


def test_status_and_manual_sync_routes(monkeypatch):
    import server
    from duanxian import growth_ladders

    monkeypatch.setattr(growth_ladders, "status", lambda: {"state": "building", "phase": "history"})
    monkeypatch.setattr(growth_ladders, "request_sync", lambda day=None: {"state": "building", "requested": day})
    client = TestClient(server.app, base_url="http://127.0.0.1")

    status = client.get("/api/market/growth-ladders/yang/status")
    sync = client.post("/api/market/growth-ladders/yang/sync?date=2026-10-09")

    assert status.json()["data"]["phase"] == "history"
    assert sync.json()["data"]["requested"] == "2026-10-09"


def test_corrupted_database_reports_explicit_failure(tmp_path, monkeypatch):
    path = tmp_path / "broken.sqlite"
    path.write_bytes(b"not-a-sqlite-database")
    monkeypatch.setenv("GROWTH_LADDERS_DB", str(path))
    from duanxian import growth_ladders

    result = growth_ladders.status()

    assert result["state"] == "failed"
    assert "数据库损坏" in result["error"]
