import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def pattern(tmp_path, monkeypatch):
    monkeypatch.setenv("GROWTH_LADDERS_DB", str(tmp_path / "pattern.sqlite"))
    from duanxian import board_pattern_research

    board_pattern_research.reset_for_tests()
    return board_pattern_research


def test_board_type_rules(pattern):
    assert pattern.classify_board_type(10, 10, 10, 10, 10) == "一字"
    assert pattern.classify_board_type(10, 10, 9.8, 10, 10) == "T字"
    assert pattern.classify_board_type(9.6, 10, 9.5, 10, 10) == "换手"
    assert pattern.classify_board_type(None, None, None, None, None, "09:25:00", 1) == "T字"


def test_market_limit_rules_and_path_suffix(pattern):
    assert pattern.market_limit_pct("600001") == 10
    assert pattern.market_limit_pct("300001") == 20
    assert pattern.market_limit_pct("688001") == 20
    assert pattern.market_limit_pct("920001") == 30
    assert pattern.market_limit_pct("600001", True) == 5
    assert pattern.same_path_suffix(["换手", "T字", "换手"], ["一字", "T字", "换手"]) == 2


def test_versioned_schema_adds_turnover_and_pattern_tables(pattern):
    with pattern._connect() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(daily_bars)")}
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "turnover_pct" in columns
    assert {"pattern_limit_events", "pattern_day_snapshots", "pattern_match_cache", "pattern_sync_runs",
            "instrument_master", "instrument_aliases", "corpus_date_coverage"} <= tables


def test_store_day_is_idempotent_and_builds_path(pattern, monkeypatch):
    monkeypatch.setattr(pattern.trade_calendar, "prev_trade_date", lambda day: {
        "2026-10-08": "2026-10-07", "2026-10-09": "2026-10-08",
    }.get(day))
    base = {
        "code": "600400", "name": "红豆股份", "market": "SH", "industry": "服装家纺",
        "theme": "", "status": "sealed", "open": 10, "high": 11, "low": 10,
        "close": 11, "prev_close": 10, "pct_chg": 10, "volume": 1, "amount": 1e8,
        "turnover_pct": 17.22, "float_mcap": 5e9, "first_seal": "10:00:00",
        "break_times": 0, "source": "fixture", "observed_at": "now", "path": [],
    }
    pattern._store_day("2026-10-08", [{**base, "event_date": "2026-10-08", "board_height": 1, "board_type": "换手"}], {"fixture": 1})
    current = {**base, "event_date": "2026-10-09", "board_height": 2, "board_type": "T字"}
    pattern._store_day("2026-10-09", [current], {"fixture": 1})
    pattern._store_day("2026-10-09", [current], {"fixture": 1})

    with pattern._connect() as db:
        rows = list(db.execute("SELECT path_json FROM pattern_limit_events WHERE event_date='2026-10-09'"))
    assert len(rows) == 1
    assert rows[0][0] == '["换手", "T字"]'


def test_local_rebuild_bulk_streak_detects_second_board(pattern, monkeypatch):
    dates = ["2026-10-08", "2026-10-09"]
    instrument = {
        "code": "600400", "name": "红豆股份", "market": "SH", "industry": "服装家纺",
        "industry_source": "fixture", "trade_status": 1, "is_st": 0,
    }
    for day in dates:
        pattern.growth_ladders._store_instruments(day, [instrument])
    pattern.growth_ladders._store_bars([
        {"code": "600400", "trade_date": dates[0], "open": 10, "high": 11, "low": 10,
         "close": 11, "pct_chg": 10, "volume": 1, "amount": 1, "trade_status": 1,
         "is_st": 0, "source": "fixture", "fetched_at": "now"},
        {"code": "600400", "trade_date": dates[1], "open": 11, "high": 12.1, "low": 11,
         "close": 12.1, "pct_chg": 10, "volume": 1, "amount": 1, "trade_status": 1,
         "is_st": 0, "source": "fixture", "fetched_at": "now"},
    ])
    monkeypatch.setattr(pattern, "_previous_events", lambda _day: {})

    events = pattern._rebuild_from_daily_bars(dates[1])

    assert len(events) == 1
    assert events[0]["code"] == "600400"
    assert events[0]["board_height"] == 2


def test_local_rebuild_persists_full_path_without_storing_first_board(pattern, monkeypatch):
    dates = ["2026-10-07", "2026-10-08", "2026-10-09"]
    previous = {dates[index]: dates[index - 1] if index else "" for index in range(len(dates))}
    monkeypatch.setattr(pattern.trade_calendar, "prev_trade_date", lambda day: previous.get(day))
    instrument = {
        "code": "600400", "name": "红豆股份", "market": "SH", "industry": "服装家纺",
        "industry_source": "fixture", "trade_status": 1, "is_st": 0,
    }
    prices = [(11.0, 11.0, 11.0, 11.0), (12.1, 12.1, 11.5, 12.1), (12.0, 13.31, 11.9, 13.31)]
    for day, (open_, high, low, close) in zip(dates, prices):
        pattern.growth_ladders._store_instruments(day, [instrument])
        pattern.growth_ladders._store_bars([{
            "code": "600400", "trade_date": day, "open": open_, "high": high, "low": low,
            "close": close, "pct_chg": 10, "volume": 1, "amount": 1, "trade_status": 1,
            "is_st": 0, "source": "fixture", "fetched_at": "now",
        }])

    for index, day in enumerate(dates):
        events = pattern._rebuild_from_daily_bars(day)
        pattern._store_day(day, events, {"fixture": len(events)}, previous_day=dates[index - 1] if index else "")

    with pattern._connect() as db:
        event = db.execute(
            "SELECT board_height,path_json FROM pattern_limit_events WHERE event_date=? AND code=?",
            (dates[-1], "600400"),
        ).fetchone()
    assert event[0] == 3
    assert event[1] == '["一字", "T字", "换手"]'


def test_confirmed_break_is_emitted_only_on_first_day_after_sealed_event(pattern, monkeypatch):
    dates = ["2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12"]
    previous = {dates[index]: dates[index - 1] if index else "" for index in range(len(dates))}
    monkeypatch.setattr(pattern.trade_calendar, "prev_trade_date", lambda day: previous.get(day))
    instrument = {
        "code": "600400", "name": "红豆股份", "market": "SH", "industry": "服装家纺",
        "industry_source": "fixture", "trade_status": 1, "is_st": 0,
    }
    changes = [10, 10, -2, 1]
    closes = [11, 12.1, 11.858, 11.97658]
    for index, day in enumerate(dates):
        close = closes[index]
        pattern.growth_ladders._store_instruments(day, [instrument])
        pattern.growth_ladders._store_bars([{
            "code": "600400", "trade_date": day, "open": close * .99, "high": close * 1.01,
            "low": close * .98, "close": close, "pct_chg": changes[index], "volume": 1,
            "amount": 1, "trade_status": 1, "is_st": 0, "source": "fixture", "fetched_at": "now",
        }])
        events = pattern._rebuild_from_daily_bars(day)
        pattern._store_day(day, events, {"fixture": len(events)}, previous_day=dates[index - 1] if index else "")

    with pattern._connect() as db:
        break_dates = [row[0] for row in db.execute(
            "SELECT event_date FROM pattern_limit_events WHERE code=? AND status='confirmed_break' ORDER BY event_date",
            ("600400",),
        )]
    assert break_dates == [dates[2]]


def test_path_never_bridges_a_missing_trading_day(pattern, monkeypatch):
    monkeypatch.setattr(pattern.trade_calendar, "prev_trade_date", lambda day: {
        "2026-10-08": "2026-10-07", "2026-10-09": "2026-10-08",
    }.get(day))
    base = {
        "code": "600400", "name": "红豆股份", "market": "SH", "industry": "服装家纺",
        "theme": "", "status": "sealed", "open": 10, "high": 11, "low": 10,
        "close": 11, "prev_close": 10, "pct_chg": 10, "volume": 1, "amount": 1e8,
        "turnover_pct": 17.22, "float_mcap": 5e9, "first_seal": "10:00:00",
        "break_times": 0, "source": "fixture", "observed_at": "now", "path": [],
    }
    pattern._store_day("2026-10-07", [{**base, "event_date": "2026-10-07", "board_height": 1, "board_type": "换手"}], {"fixture": 1})
    pattern._store_day("2026-10-09", [{**base, "event_date": "2026-10-09", "board_height": 2, "board_type": "T字"}], {"fixture": 1})

    with pattern._connect() as db:
        path_json = db.execute(
            "SELECT path_json FROM pattern_limit_events WHERE event_date='2026-10-09'"
        ).fetchone()[0]
    assert path_json == '["T字"]'


def test_break_event_inherits_the_immediately_previous_board_path(pattern, monkeypatch):
    monkeypatch.setattr(pattern.trade_calendar, "prev_trade_date", lambda day: {
        "2026-10-08": "2026-10-07", "2026-10-09": "2026-10-08",
    }.get(day))
    base = {
        "code": "600400", "name": "红豆股份", "market": "SH", "industry": "服装家纺",
        "theme": "", "open": 10, "high": 11, "low": 10, "close": 11,
        "prev_close": 10, "pct_chg": 10, "volume": 1, "amount": 1e8,
        "turnover_pct": 17.22, "float_mcap": 5e9, "first_seal": "10:00:00",
        "break_times": 0, "source": "fixture", "observed_at": "now", "path": [],
    }
    pattern._store_day("2026-10-07", [{**base, "event_date": "2026-10-07", "status": "sealed", "board_height": 1, "board_type": "换手"}], {"fixture": 1})
    pattern._store_day("2026-10-08", [{**base, "event_date": "2026-10-08", "status": "sealed", "board_height": 2, "board_type": "T字"}], {"fixture": 1})
    pattern._store_day("2026-10-09", [{**base, "event_date": "2026-10-09", "status": "confirmed_break", "board_height": 2, "board_type": "断板"}], {"fixture": 1})

    with pattern._connect() as db:
        path_json = db.execute(
            "SELECT path_json FROM pattern_limit_events WHERE event_date='2026-10-09'"
        ).fetchone()[0]
    assert path_json == '["换手", "T字"]'


def test_historical_sync_uses_local_corpus_and_exact_target_dates(pattern, monkeypatch):
    dates = ["2026-10-07", "2026-10-08", "2026-10-09"]
    monkeypatch.setattr(pattern, "HISTORY_SESSIONS", 2)
    monkeypatch.setattr(pattern, "_session_dates", lambda _end: dates)
    monkeypatch.setattr(pattern, "_fetch_events", lambda *_args, **_kwargs: pytest.fail(
        "historical close sync must not call the Eastmoney live pools"
    ))
    phases = []

    def ensure_window(requested, progress_callback=None):
        assert requested == dates
        for phase in ("corpus_universe", "corpus_bars"):
            phases.append(phase)
            progress_callback({"phase": phase, "completed": 3, "total": 3})
        return {
            "required_dates": dates, "ready_dates": dates, "missing_dates": [],
            "coverage": {day: {"universe_count": 1, "covered_count": 1, "unknown_count": 0} for day in dates},
            "source_hits": {"fixture": 3},
        }

    monkeypatch.setattr(pattern.growth_ladders, "ensure_corpus_window", ensure_window)
    rebuilt = []
    monkeypatch.setattr(pattern, "_rebuild_from_daily_bars", lambda day, live=False: rebuilt.append(day) or [])
    with pattern._transaction() as db:
        db.execute(
            "INSERT INTO pattern_day_snapshots VALUES(?,?,?,?,?,?,?)",
            (dates[1], "board-pattern-v1", '{"coverage":"full","events":[]}', "full", 0, "{}", "old"),
        )
    pattern._new_run(dates[-1])
    pattern._sync_once(dates[-1])

    assert rebuilt == dates
    assert phases == ["corpus_universe", "corpus_bars"]
    with pattern._connect() as db:
        snapshots = list(db.execute(
            "SELECT trade_date,algorithm_version FROM pattern_day_snapshots ORDER BY trade_date"
        ))
    assert [(row[0], row[1]) for row in snapshots] == [
        (day, pattern.ALGORITHM_VERSION) for day in dates
    ]
    sync = pattern.status()
    assert sync["state"] == "ready"
    assert sync["quality"]["snapshot_ready_dates"] == 2
    assert sync["quality"]["required_dates"] == 2


def test_sync_persists_unknown_corpus_failures(pattern, monkeypatch):
    day = "2026-10-09"
    monkeypatch.setattr(pattern, "HISTORY_SESSIONS", 1)
    monkeypatch.setattr(pattern, "_session_dates", lambda _end: [day])
    monkeypatch.setattr(pattern.growth_ladders, "ensure_corpus_window", lambda _dates, _callback: {
        "required_dates": [day], "ready_dates": [], "missing_dates": [day],
        "coverage": {day: {"universe_count": 2, "covered_count": 1, "unknown_count": 1,
                           "unknown_codes": ["920001"]}},
        "source_hits": {"fixture": 1},
        "fetch_failures": [{"code": "920001", "provider": "eastmoney_history",
                            "category": "provider_network", "message": "timeout", "retriable": True}],
    })
    pattern._new_run(day)

    with pytest.raises(pattern.SyncFailure):
        pattern._sync_once(day)

    sync = pattern.status()
    assert sync["state"] == "failed"
    assert sync["failure_summary"]["retriable_count"] == 1
    assert sync["failure_summary"]["samples"][0]["trade_date"] == day
    assert sync["failure_summary"]["samples"][0]["phase"] == "corpus_bars"
    assert sync["failure_summary"]["categories"] == {"provider_network": 1}


def test_status_quality_reads_materialized_coverage(pattern, monkeypatch):
    day = "2026-10-09"
    monkeypatch.setattr(pattern, "HISTORY_SESSIONS", 1)
    monkeypatch.setattr(pattern, "PRELUDE_SESSIONS", 0)
    with pattern._transaction() as db:
        db.execute(
            "INSERT INTO corpus_date_coverage VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (day, 5000, 4999, 20, 4, 1, 96, 3, '{"baostock":4999}',
             pattern.growth_ladders.ALGORITHM_VERSION, "2026-10-13T10:00:00+08:00"),
        )
        db.execute(
            "INSERT INTO instrument_master(canonical_code,source_code,exchange,security_type,name,current_status,"
            "aliases_json,source,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("600000", "sh.600000", "SH", "stock", "浦发银行", "active", "[]", "fixture", "now"),
        )

    result = pattern.status()["quality"]

    assert result["bars_ready_dates"] == 0
    assert result["unknown_gap_count"] == 1
    assert result["invalid_directory_count"] == 96
    assert result["alias_merged_count"] == 3
    assert result["canonical_instrument_count"] == 1


def test_complete_history_stays_matchable_during_next_daily_increment(pattern, monkeypatch):
    monkeypatch.setattr(pattern, "HISTORY_SESSIONS", 1)
    day = "2026-10-13"
    with pattern._transaction() as db:
        db.execute(
            "INSERT INTO corpus_date_coverage VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (day, 1, 1, 0, 0, 0, 0, 0, '{}', pattern.growth_ladders.ALGORITHM_VERSION, "now"),
        )
    pattern._store_day(day, [], {"fixture": 1}, coverage="full")
    pattern._new_run("2026-10-14")

    payload = pattern._events_response(
        day, [{"code": "600400", "name": "红豆股份", "status": "sealed",
               "board_height": 2, "board_type": "换手", "industry": "服装家纺"}],
        live=False, coverage="full", source_hits={"fixture": 1},
    )

    assert payload["sync"]["state"] == "queued"
    assert payload["quality"]["snapshot_ready_dates"] == 1
    assert payload["matching_available"] is True


def test_score_intraday_deweights_unfinished_volume(pattern, monkeypatch):
    monkeypatch.setattr(pattern, "_environment_for", lambda _day: {
        "limit_up_count": 60, "break_rate": 20, "max_height": 6, "promotion_rate": 25, "index_pct": 1,
    })
    target = {"event_date": "2026-10-09", "code": "600001", "board_height": 2,
              "path": ["换手", "T字"], "pct_chg": 10, "turnover_pct": 15, "amount": 2e8, "open": 10}
    candidate = {**target, "event_date": "2026-09-09", "code": "600002",
                 "turnover_pct": 2, "amount": 1e7, "pct_chg": 1, "open": 5}
    close = pattern.score_case(target, candidate, "v1", intraday=False)
    intraday = pattern.score_case(target, candidate, "v1", intraday=True)
    assert intraday["volume_price"] > close["volume_price"]
    assert intraday["liquidity"] > close["liquidity"]


def test_live_events_complete_temporary_break_path_from_historical_bars(pattern, monkeypatch):
    dates = ["2026-10-10", "2026-10-12", "2026-10-13"]
    instrument = {
        "code": "000892", "name": "欢瑞世纪", "market": "SZ", "industry": "影视院线",
        "industry_source": "fixture", "trade_status": 1, "is_st": 0,
    }
    prior_bars = [
        (dates[0], 4.9, 5.5, 4.8, 5.5),
        (dates[1], 6.05, 6.05, 5.7, 6.05),
    ]
    for day, open_, high, low, close in prior_bars:
        pattern.growth_ladders._store_instruments(day, [instrument])
        pattern.growth_ladders._store_bars([{
            "code": "000892", "trade_date": day, "open": open_, "high": high, "low": low,
            "close": close, "pct_chg": 10, "volume": 1, "amount": 1, "trade_status": 1,
            "is_st": 0, "source": "fixture", "fetched_at": "now",
        }])
    live_event = {
        "event_date": dates[-1], "code": "000892", "name": "欢瑞世纪", "market": "SZ",
        "industry": "影视院线", "theme": "", "status": "temporary_break", "board_height": 2,
        "board_type": "断板", "path": [], "open": 5.9, "high": 6.0, "low": 5.5, "close": 5.7,
        "prev_close": 6.05, "pct_chg": -5.79, "volume": 1, "amount": 1,
        "turnover_pct": 20, "float_mcap": None, "first_seal": "", "break_times": 0,
        "source": "fixture", "observed_at": "now",
    }
    monkeypatch.setattr(pattern, "_fetch_live_events", lambda _day: ([live_event], {"fixture": 1}))
    monkeypatch.setattr(pattern, "status", lambda: {
        "state": "ready", "quality": {"snapshot_ready_dates": pattern.HISTORY_SESSIONS,
                                         "required_dates": pattern.HISTORY_SESSIONS},
    })
    monkeypatch.setattr(pattern, "_available_dates", lambda: dates)
    pattern._LIVE_CACHE.clear()

    result = pattern._live_events(dates[-1])

    assert result["events"][0]["path"] == ["换手", "T字"]


def test_live_events_complete_sealed_path_without_today_daily_bar(pattern, monkeypatch):
    dates = ["2026-10-13", "2026-10-14"]
    instrument = {
        "code": "000823", "name": "超声电子", "market": "SZ", "industry": "元件",
        "industry_source": "fixture", "trade_status": 1, "is_st": 0,
    }
    pattern.growth_ladders._store_instruments(dates[0], [instrument])
    pattern.growth_ladders._store_bars([{
        "code": "000823", "trade_date": dates[0], "open": 10, "high": 11, "low": 9.9,
        "close": 11, "pct_chg": 10, "volume": 1, "amount": 1, "trade_status": 1,
        "is_st": 0, "source": "fixture", "fetched_at": "now",
    }])
    event = {
        "event_date": dates[1], "code": "000823", "name": "超声电子", "market": "SZ",
        "industry": "元件", "theme": "", "status": "sealed", "board_height": 2,
        "board_type": "T字", "path": [], "open": 11.5, "high": 12.1, "low": 11.3,
        "close": 12.1, "prev_close": 11, "pct_chg": 10, "volume": 1, "amount": 1,
        "turnover_pct": 5, "float_mcap": None, "first_seal": "09:25:00", "break_times": 1,
        "source": "fixture", "observed_at": "now",
    }
    monkeypatch.setattr(pattern, "_fetch_live_events", lambda _day: ([event], {"fixture": 1}))
    monkeypatch.setattr(pattern, "status", lambda: {
        "state": "ready", "quality": {"snapshot_ready_dates": pattern.HISTORY_SESSIONS,
                                         "required_dates": pattern.HISTORY_SESSIONS},
    })
    monkeypatch.setattr(pattern, "_available_dates", lambda: dates)
    pattern._LIVE_CACHE.clear()

    result = pattern._live_events(dates[-1])

    assert result["events"][0]["path"] == ["换手", "T字"]


def test_intraday_events_are_unavailable_before_0925(pattern, monkeypatch):
    monkeypatch.setattr(pattern, "china_now", lambda: pattern.datetime(2026, 10, 14, 9, 24))
    monkeypatch.setattr(pattern, "_live_events", lambda _day: pytest.fail("09:25前不得生成盘中事件"))

    result = pattern.events("intraday")

    assert result["coverage"] == "unavailable"
    assert result["available"] is False
    assert result["events"] == []
    assert result["intraday_window"]["available"] is False
    assert "09:25" in result["note"]


def test_intraday_events_open_at_0925_with_current_session_quote(pattern, monkeypatch):
    day = "2026-10-14"
    monkeypatch.setattr(pattern, "china_now", lambda: pattern.datetime(2026, 10, 14, 9, 25))
    monkeypatch.setattr(pattern.trade_calendar, "quote_trade_day", lambda: day)
    monkeypatch.setattr(pattern, "_live_events", lambda requested: {"date": requested, "coverage": "full"})

    result = pattern.events("intraday")

    assert result == {"date": day, "coverage": "full"}


def test_intraday_calculate_is_blocked_before_0925(pattern, monkeypatch):
    monkeypatch.setattr(pattern, "china_now", lambda: pattern.datetime(2026, 10, 14, 9, 24))
    monkeypatch.setattr(pattern, "_target_event", lambda *_args: pytest.fail("09:25前不得进入匹配计算"))

    with pytest.raises(RuntimeError, match="09:25"):
        pattern.calculate("000892", "intraday", "v1")


def test_live_provider_failure_returns_unavailable_payload(pattern, monkeypatch):
    day = "2026-10-14"
    monkeypatch.setattr(pattern, "china_now", lambda: pattern.datetime(2026, 10, 14, 9, 31))
    monkeypatch.setattr(pattern, "_fetch_live_events", lambda _day: (_ for _ in ()).throw(RuntimeError("pool down")))
    monkeypatch.setattr(pattern, "status", lambda: {"state": "ready", "quality": {}})
    monkeypatch.setattr(pattern, "_available_dates", lambda: [])
    pattern._LIVE_CACHE.clear()

    result = pattern._live_events(day)

    assert result["coverage"] == "unavailable"
    assert result["events"] == []
    assert "pool down" in result["note"]


def test_calculate_rejects_incomplete_live_path_instead_of_returning_zero_samples(pattern, monkeypatch):
    day = "2026-10-14"
    monkeypatch.setattr(pattern, "china_now", lambda: pattern.datetime(2026, 10, 14, 9, 31))
    monkeypatch.setattr(pattern.trade_calendar, "quote_trade_day", lambda: day)
    target = {
        "code": "002860", "name": "星帅尔", "status": "sealed", "board_height": 2,
        "board_type": "一字", "path": [],
    }
    monkeypatch.setattr(pattern, "_target_event", lambda *_args: (
        target, {"matching_available": True, "coverage": "full", "anchor_time": "now", "quality": {}},
    ))

    with pytest.raises(RuntimeError, match="逐板路径尚未完整"):
        pattern.calculate("002860", "intraday", "v1")


def test_live_pool_keeps_previous_high_board_with_current_quote(pattern, monkeypatch):
    from vr import astock

    day = "2026-10-14"
    prior = {
        "event_date": "2026-10-13", "code": "000892", "name": "欢瑞世纪", "market": "SZ",
        "industry": "影视院线", "theme": "", "status": "sealed", "board_height": 2,
        "board_type": "T字", "path_json": '["换手", "T字"]', "open": 5.5, "high": 6.05,
        "low": 5.4, "close": 6.05, "prev_close": 5.5, "pct_chg": 10, "volume": 1,
        "amount": 1, "turnover_pct": 10, "float_mcap": 1, "first_seal": "10:00:00",
        "break_times": 0, "source": "fixture", "observed_at": "now",
    }
    monkeypatch.setattr(astock, "em_zt_topic_observed", lambda *_args, **_kwargs: {
        "available": True, "rows": [], "reason": "",
    })
    monkeypatch.setattr(astock, "tencent_quote", lambda _codes: {"000892": {
        "name": "欢瑞世纪", "quote_time": "20261014093100", "price": 5.8, "last_close": 6.05,
        "open": 5.9, "high": 6.0, "low": 5.7, "change_pct": -4.13, "volume_hands": 100,
        "amount_wan": 580, "turnover_pct": 2.1, "float_mcap_yi": 42, "limit_up": 6.66,
    }})
    monkeypatch.setattr(pattern, "_parse_pools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(pattern, "_previous_events", lambda _day: {"000892": prior})

    items, hits = pattern._fetch_live_events(day)

    assert len(items) == 1
    assert items[0]["status"] == "temporary_break"
    assert items[0]["board_height"] == 2
    assert items[0]["close"] == 5.8
    assert hits["tencent_live"] == 1


def test_outcome_and_scripts(pattern):
    assert pattern.outcome_label({"t1": 10, "t1_high": 10}, "600001") == "强修复/晋级"
    assert pattern.outcome_label({"t1": -10, "t1_high": -2}, "600001") == "继续被核"
    assert [row["title"] for row in pattern.verification_scripts("sealed")] == ["继续晋级", "分歧承接", "晋级失败"]
    assert [row["title"] for row in pattern.verification_scripts("confirmed_break")] == ["强修复", "独立反抽", "继续退潮"]


def test_stats_keeps_limit_threshold_aligned_when_a_case_has_no_t1(pattern):
    cases = [
        {"code": "300001", "result": {"t1": None, "t1_high": None}},
        {"code": "600001", "result": {"t1": 10.0, "t1_high": 10.0}},
    ]
    stats = pattern._stats(cases, complete_count=2)
    assert stats["next_limit_up"] == 1
    assert stats["next_limit_down"] == 0


def test_first_calculation_reuses_one_database_connection_for_all_candidates(pattern, monkeypatch):
    target_day = "2026-10-13"
    candidate_day = "2026-10-01"
    target = {
        "event_date": target_day, "code": "600400", "name": "红豆股份", "market": "SH",
        "industry": "服装家纺", "theme": "", "status": "sealed", "board_height": 2,
        "board_type": "换手", "path": ["换手", "换手"], "open": 10.0, "high": 11.0,
        "low": 9.9, "close": 11.0, "prev_close": 10.0, "pct_chg": 10.0,
        "volume": 1_000_000, "amount": 100_000_000, "turnover_pct": 10.0,
    }
    bars = []
    with pattern._transaction() as db:
        for index in range(24):
            code = f"60{index + 100:04d}"
            db.execute(
                "INSERT INTO pattern_limit_events("
                "event_date,code,name,market,industry,theme,status,board_height,board_type,path_json,"
                "open,high,low,close,prev_close,pct_chg,volume,amount,turnover_pct,float_mcap,"
                "first_seal,break_times,source,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (candidate_day, code, f"样本{index}", "SH", "测试", "", "sealed", 2, "换手",
                 '["换手", "换手"]', 10, 11, 9.9, 11, 10, 10, 1_000_000, 100_000_000,
                 10, 1_000_000_000, "10:00:00", 0, "fixture", "now"),
            )
            for offset, day in enumerate(("2026-10-08", "2026-10-09", "2026-10-12"), 1):
                bars.append({
                    "code": code, "trade_date": day, "open": 11 + offset, "high": 12 + offset,
                    "low": 10 + offset, "close": 11.5 + offset, "pct_chg": 1, "volume": 1,
                    "amount": 1, "trade_status": 1, "is_st": 0, "source": "fixture", "fetched_at": "now",
                })
    pattern.growth_ladders._store_bars(bars)
    monkeypatch.setattr(pattern, "_target_event", lambda *_args: (
        target,
        {"matching_available": True, "coverage": "full", "anchor_time": "now", "quality": {}},
    ))
    monkeypatch.setattr(pattern.trade_calendar, "latest_session", lambda: target_day)

    original_connect = pattern._connect
    connection_count = 0

    def counted_connect():
        nonlocal connection_count
        connection_count += 1
        return original_connect()

    monkeypatch.setattr(pattern, "_connect", counted_connect)
    result = pattern.calculate("600400", "close", "v1", target_day)

    assert result["candidate_count"] == 24
    assert connection_count <= 1, "只读候选计算必须复用连接，不能按候选反复打开 SQLite"


def test_fixed_samples_redbean_and_longban(pattern, monkeypatch):
    monkeypatch.setattr(pattern, "_previous_events", lambda _day: {})
    monkeypatch.setattr(pattern, "_bar_map", lambda _day, _codes: {})
    redbean = pattern._parse_pools("2026-10-09", [{
        "c": "600400", "n": "红豆股份", "p": 11000, "zdp": 10,
        "lbc": 2, "fbt": 100000, "zbc": 0, "hs": 17.22, "hybk": "服装家纺",
    }], [], live=False)[0]
    longban = pattern._parse_pools("2026-10-13", [{
        "c": "605577", "n": "龙版传媒", "p": 20000, "zdp": 4.99,
        "lbc": 2, "fbt": 92500, "zbc": 1, "hs": 12.3, "hybk": "出版业",
    }], [], live=True)[0]
    assert redbean["board_height"] == 2 and redbean["board_type"] == "换手"
    assert longban["board_height"] == 2 and longban["board_type"] == "T字"


def test_intraday_matching_uses_completed_history_without_today_snapshot(pattern, monkeypatch):
    monkeypatch.setattr(pattern, "_available_dates", lambda: [f"d{index}" for index in range(pattern.HISTORY_SESSIONS)])
    monkeypatch.setattr(pattern, "status", lambda: {"state": "ready", "phase": "complete"})
    response = pattern._events_response("2026-10-13", [], live=True, coverage="full", source_hits={})
    assert response["matching_available"] is True


def test_api_contracts(monkeypatch):
    import server
    from duanxian import board_pattern_research

    expected = {"available": True, "coverage": "full", "matching_available": True, "date": "2026-10-09", "events": []}
    monkeypatch.setattr(board_pattern_research, "events", lambda mode="close", trade_date=None: {**expected, "mode": mode, "date": trade_date or expected["date"]})
    monkeypatch.setattr(board_pattern_research, "search", lambda *args: [{"code": "600400", "name": "红豆股份"}])
    monkeypatch.setattr(board_pattern_research, "calculate", lambda *args: {"available": True, "algorithm": args[2]})
    monkeypatch.setattr(board_pattern_research, "status", lambda: {"state": "ready", "phase": "complete"})
    monkeypatch.setattr(board_pattern_research, "request_sync", lambda date=None: {"state": "queued", "requested": date})
    client = TestClient(server.app, base_url="http://127.0.0.1")

    assert client.get("/api/market/board-pattern-research/events?date=2026-10-09").json()["data"]["date"] == "2026-10-09"
    assert client.get("/api/market/board-pattern-research/search?q=红豆").json()["data"][0]["code"] == "600400"
    assert client.post("/api/market/board-pattern-research/calculate", json={"code": "600400", "mode": "close", "algorithm": "v2"}).json()["data"]["algorithm"] == "v2"
    assert client.get("/api/market/board-pattern-research/status").json()["data"]["state"] == "ready"
    assert client.post("/api/market/board-pattern-research/sync?date=2026-10-09").json()["data"]["requested"] == "2026-10-09"
