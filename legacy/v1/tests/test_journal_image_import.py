from __future__ import annotations

import json

import pytest


def _journal(tmp_path, monkeypatch):
    from duanxian import journal

    monkeypatch.setattr(journal, "_DIR", str(tmp_path))
    monkeypatch.setattr(journal, "_PATH", str(tmp_path / "trades.json"))
    monkeypatch.setattr(journal, "_market_context", lambda _day: {})
    monkeypatch.setattr(journal, "_stock_context", lambda _day, _code: {})
    monkeypatch.setattr(journal, "load_fees", lambda: {
        "commission_rate": 0, "commission_min": 0,
        "stamp_tax_rate": 0, "transfer_fee_rate": 0,
    })
    return journal


def _sample_lines():
    return [
        {"text": "播恩集团", "score": .99},
        {"text": "清仓 2026-09-10 -1,273.49", "score": .98},
        {"text": "价格 13.950 金额 6,975.00", "score": .97},
        {"text": "数量 -500 税费 8.49", "score": .97},
        {"text": "买入 2026-09-09", "score": .98},
        {"text": "价格 15.080 金额 3,016.00", "score": .97},
        {"text": "数量 200 费用 5.00", "score": .97},
        {"text": "建仓 2026-09-08", "score": .98},
        {"text": "价格 17.380 金额 5,214.00", "score": .97},
        {"text": "数量 300 费用 5.00", "score": .97},
    ]


def test_generic_parser_extracts_sample_broker_record():
    from duanxian import journal_import

    result = journal_import._parse_lines(
        _sample_lines(), [{"code": "001366", "name": "播恩集团", "market": "SZ"}]
    )
    assert result["code"] == "001366"
    assert result["name"] == "播恩集团"
    assert [(fill["side"], fill["date"], fill["price"], fill["shares"], fill["fee"])
            for fill in result["fills"]] == [
        ("sell", "2026-09-10", 13.95, 500.0, 8.49),
        ("buy", "2026-09-09", 15.08, 200.0, 5.0),
        ("buy", "2026-09-08", 17.38, 300.0, 5.0),
    ]
    assert result["reported_pnl"] == -1273.49


def test_parser_sums_profit_from_every_exit_in_the_same_screenshot():
    from duanxian import journal, journal_import

    lines = [
        {"text": "万向德农", "score": .99},
        {"text": "清仓 2026-10-08 288.91", "score": .99},
        {"text": "价格 13.920 金额 4,176.00", "score": .99},
        {"text": "数量 -300 税费 7.09", "score": .99},
        {"text": "卖出 2026-10-08 312.90", "score": .99},
        {"text": "价格 14.000 金额 4,200.00", "score": .99},
        {"text": "数量 -300 税费 7.10", "score": .99},
        {"text": "买入 2026-09-30", "score": .99},
        {"text": "价格 12.910 金额 2,582.00", "score": .99},
        {"text": "数量 200 费用 5.00", "score": .99},
        {"text": "建仓 2026-09-30", "score": .99},
        {"text": "价格 12.920 金额 5,168.00", "score": .99},
        {"text": "数量 400 费用 5.00", "score": .99},
    ]
    result = journal_import._parse_lines(
        lines, [{"code": "600371", "name": "万向德农", "market": "SH"}]
    )

    assert result["reported_pnl"] == 601.81
    assert [fill["side"] for fill in result["fills"]] == ["sell", "sell", "buy", "buy"]
    assert journal._settle(result["fills"])["realized_pnl"] == 601.81


def test_commit_batch_uses_actual_fees_and_records_only_hashes(tmp_path, monkeypatch):
    journal = _journal(tmp_path, monkeypatch)
    fills = [{key: value for key, value in fill.items() if key in {"side", "date", "price", "shares", "fee"}}
             for fill in __import__("duanxian.journal_import", fromlist=["x"])._parse_lines(
                 _sample_lines(), [{"code": "001366", "name": "播恩集团"}]
             )["fills"]]
    result = journal.commit_image_import([{
        "action": "create", "source_hash": "a" * 64, "code": "001366",
        "name": "播恩集团", "playbook": "其它", "fills": fills,
    }])
    assert result["created"] == 1
    trade = journal.all_trades()[0]
    assert trade["settled"]["realized_pnl"] == -1273.49
    assert trade["settled"]["fees"] == 18.49
    assert trade["import_meta"]["source_hashes"] == ["a" * 64]
    raw = (tmp_path / "trades.json").read_text(encoding="utf-8")
    assert "播恩集团" in raw
    assert "OCR 原文" not in raw and "content_b64" not in raw


def test_append_preserves_plan_and_recalculates(tmp_path, monkeypatch):
    journal = _journal(tmp_path, monkeypatch)
    original = journal.add_trade(
        "2026-09-08", "001366", "播恩集团", "低吸", note="原计划",
        planned_stop=16.0,
        fills=[{"side": "buy", "date": "2026-09-08", "price": 17.38, "shares": 300, "fee": 5}],
    )["trade"]
    result = journal.commit_image_import([{
        "action": "append", "trade_id": original["id"], "source_hash": "b" * 64,
        "code": "001366", "name": "播恩集团", "playbook": "其它",
        "fills": [{"side": "sell", "date": "2026-09-10", "price": 13.95, "shares": 300, "fee": 8.49}],
    }])
    assert result["updated"] == 1
    updated = journal.all_trades()[0]
    assert updated["created_at"] == original["created_at"]
    assert updated["planned_stop"] == 16.0
    assert updated["note"] == "原计划"
    assert updated["settled"]["closed"] is True


def test_invalid_later_item_keeps_batch_atomic(tmp_path, monkeypatch):
    journal = _journal(tmp_path, monkeypatch)
    before = journal.add_trade("2026-09-01", "600000", "浦发银行", "其它")["trade"]
    old_bytes = (tmp_path / "trades.json").read_bytes()
    with pytest.raises(ValueError, match="第 2 条"):
        journal.commit_image_import([
            {"action": "create", "source_hash": "c" * 64, "code": "001366", "name": "播恩集团",
             "fills": [{"side": "buy", "date": "2026-09-08", "price": 10, "shares": 100}]},
            {"action": "create", "source_hash": "d" * 64, "code": "002463", "name": "沪电股份",
             "fills": [{"side": "sell", "date": "2026-09-08", "price": 10, "shares": 100}]},
        ])
    assert (tmp_path / "trades.json").read_bytes() == old_bytes
    assert journal.all_trades()[0]["id"] == before["id"]


def test_duplicate_requires_explicit_override(tmp_path, monkeypatch):
    journal = _journal(tmp_path, monkeypatch)
    item = {"action": "create", "source_hash": "e" * 64, "code": "001366", "name": "播恩集团",
            "fills": [{"side": "buy", "date": "2026-09-08", "price": 10, "shares": 100}]}
    journal.commit_image_import([item])
    with pytest.raises(journal.JournalImportConflict):
        journal.commit_image_import([item])
    result = journal.commit_image_import([{**item, "allow_duplicate": True}])
    assert result["created"] == 1
    assert len(journal.all_trades()) == 2


def test_schema_three_migration_adds_empty_import_metadata(tmp_path, monkeypatch):
    journal = _journal(tmp_path, monkeypatch)
    (tmp_path / "trades.json").write_text(json.dumps({
        "schema": 3, "trades": [{"id": "old", "date": "2026-09-01", "code": "600000",
                                  "name": "浦发", "playbook": "其它", "fills": [], "settled": {}}],
    }), encoding="utf-8")
    trade = journal.all_trades()[0]
    assert trade["import_meta"] is None
    assert (tmp_path / "trades.v3.bak.json").exists()
