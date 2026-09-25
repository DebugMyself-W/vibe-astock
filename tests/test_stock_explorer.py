from duanxian import stock_explorer as explorer


def test_ladder_details_returns_exact_stocks_grouped_by_board(monkeypatch):
    monkeypatch.setattr(explorer.market_facts, "pools", lambda date: {
        "zt": [
            {"code": "600001", "name": "五板甲", "boards": 5, "ret": 10.01,
             "amount": 8e8, "turnover": 12.3, "sector": "汽车零部", "first_seal": "093100",
             "last_seal": "145500", "broken_times": 2, "seal_fund": 1e8},
            {"code": "000002", "name": "二板乙", "boards": 2, "ret": 9.98,
             "amount": 3e8, "turnover": 5.6, "sector": "汽车零部", "first_seal": "101500"},
            {"code": "000003", "name": "首板丙", "boards": 1, "ret": 10.02, "sector": "软件"},
        ],
        "raw": {"zt": [
            {"代码": "600001", "最新价": 12.34, "流通市值": 4.5e9},
            {"代码": "000002", "最新价": 8.76, "流通市值": 2.1e9},
        ]},
    })

    result = explorer.ladder_details("2026-09-17")

    assert result["available"] is True
    assert result["count"] == 2
    assert [(tier["boards"], tier["count"]) for tier in result["tiers"]] == [(5, 1), (2, 1)]
    assert result["tiers"][0]["stocks"][0]["name"] == "五板甲"
    assert result["tiers"][0]["stocks"][0]["seal_time"] == "09:31"
    assert result["tiers"][0]["stocks"][0]["price"] == 12.34


def test_industry_peers_matches_truncated_industry_name(monkeypatch):
    explorer.reset_memory_for_tests()

    def fake_clist(fs, _fid, _fields, **_kwargs):
        if fs == "m:90 t:2":
            return [{"f12": "BK0481", "f14": "汽车零部件", "f3": 2.4}]
        if fs == "b:BK0481":
            return [
                {"f12": "600001", "f14": "甲", "f2": 10, "f3": 9.9, "f6": 1e8,
                 "f20": 5e9, "f21": 3e9, "f100": "汽车零部件"},
                {"f12": "000002", "f14": "乙", "f2": 8, "f3": -1.2, "f6": 8e7,
                 "f20": 4e9, "f21": 2e9, "f100": "汽车零部件"},
            ]
        return []

    monkeypatch.setattr(explorer.fetchers, "_clist", fake_clist)
    monkeypatch.setattr(explorer.trade_calendar, "quote_trade_day", lambda: "2026-09-18")

    result = explorer.industry_peers("汽车零部", "600001")

    assert result["available"] is True
    assert result["board"]["name"] == "汽车零部件"
    assert result["quote_date"] == "2026-09-18"
    assert [row["code"] for row in result["members"]] == ["600001", "000002"]
    assert result["members"][0]["is_focus"] is True


def test_intraday_defaults_to_confirmed_quote_day(monkeypatch):
    monkeypatch.setattr(explorer.trade_calendar, "quote_trade_day", lambda: "2026-09-18")
    monkeypatch.setattr(explorer, "_one_intraday", lambda code, date: {
        "code": code, "date": date, "points": [{"time": "09:35", "price": 10, "volume": 1}],
    })

    result = explorer.intraday_series(["600001", "600001", "000002"])

    assert result["requested_date"] == "2026-09-18"
    assert set(result["series"]) == {"600001", "000002"}
    assert all(line["date"] == "2026-09-18" for line in result["series"].values())
