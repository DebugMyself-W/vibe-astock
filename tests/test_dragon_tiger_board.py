from fastapi.testclient import TestClient


def _route(server):
    return next(
        route for route in server.app.routes
        if getattr(route, "path", "") == "/api/market/dragon-tiger-board"
    )


def test_daily_dragon_tiger_merges_duplicate_reasons_and_normalizes_money(monkeypatch):
    import server
    from duanxian import theme_reasons

    astock = _route(server).endpoint.__globals__["astock"]
    details = [
        {
            "TRADE_DATE": "2026-10-09", "SECURITY_CODE": "600001", "SECURITY_NAME_ABBR": "甲公司",
            "CLOSE_PRICE": 12.3, "CHANGE_RATE": 9.98, "TURNOVERRATE": 11.2,
            "BILLBOARD_DEAL_AMT": 300_000_000, "BILLBOARD_BUY_AMT": 180_000_000,
            "BILLBOARD_SELL_AMT": 120_000_000, "BILLBOARD_NET_AMT": 60_000_000,
            "EXPLANATION": "日涨幅偏离值达7%",
        },
        {
            "TRADE_DATE": "2026-10-09", "SECURITY_CODE": "600001", "SECURITY_NAME_ABBR": "甲公司",
            "CLOSE_PRICE": 12.3, "CHANGE_RATE": 9.98, "TURNOVERRATE": 11.2,
            "BILLBOARD_DEAL_AMT": 300_000_000, "BILLBOARD_BUY_AMT": 180_000_000,
            "BILLBOARD_SELL_AMT": 120_000_000, "BILLBOARD_NET_AMT": 60_000_000,
            "EXPLANATION": "日换手率达20%",
        },
        {
            "TRADE_DATE": "2026-10-09", "SECURITY_CODE": "000002", "SECURITY_NAME_ABBR": "乙公司",
            "CLOSE_PRICE": 8.1, "CHANGE_RATE": -6.1, "TURNOVERRATE": 8.2,
            "BILLBOARD_DEAL_AMT": 100_000_000, "BILLBOARD_BUY_AMT": 30_000_000,
            "BILLBOARD_SELL_AMT": 50_000_000, "BILLBOARD_NET_AMT": -20_000_000,
            "EXPLANATION": "日跌幅偏离值达7%",
        },
    ]
    buys = [{
        "SECURITY_CODE": "600001", "OPERATEDEPT_NAME": "机构专用", "OPERATEDEPT_CODE": "0",
        "BUY": 30_000_000, "SELL": 2_000_000, "NET": 28_000_000, "TOTAL_BUYRIO": 10,
    }]
    sells = [{
        "SECURITY_CODE": "600001", "OPERATEDEPT_NAME": "沪股通专用",
        "BUY": 1_000_000, "SELL": 20_000_000, "NET": -19_000_000, "TOTAL_SELLRIO": 6.67,
    }]

    def datacenter(report_name, **kwargs):
        assert kwargs["page_number"] == 1
        return {
            "RPT_DAILYBILLBOARD_DETAILSNEW": details,
            "RPT_BILLBOARD_DAILYDETAILSBUY": buys,
            "RPT_BILLBOARD_DAILYDETAILSSELL": sells,
        }[report_name]

    monkeypatch.setattr(astock, "eastmoney_datacenter", datacenter)
    monkeypatch.setattr(astock, "em_zt_topic_pool", lambda *args, **kwargs: [{"c": "600001", "lbc": 2}])
    monkeypatch.setattr(theme_reasons, "get_reasons", lambda day: ({"600001": "机器人+新型工业化"}, None))

    result = astock.daily_dragon_tiger("2026-10-09")

    assert result["total_records"] == 3
    assert result["total_stocks"] == 2
    first = result["stocks"][0]
    assert first["code"] == "600001"
    assert first["net_wan"] == 6000
    assert first["deal_wan"] == 30000
    assert first["reasons"] == ["日涨幅偏离值达7%", "日换手率达20%"]
    assert first["concept"] == "机器人"
    assert first["board_label"] == "2连板"
    assert first["buy_branches"][0]["tags"] == ["机构专用"]
    assert first["sell_branches"][0]["tags"] == ["沪股通专用"]


def test_branch_ratio_and_public_seat_profiles_are_normalized():
    import server

    astock = _route(server).endpoint.__globals__["astock"]
    quant = astock._branch_row({
        "OPERATEDEPT_NAME": "国泰海通证券股份有限公司总部",
        "BUY": 73_372_044.75, "SELL": 2_000_000, "NET": 71_372_044.75,
        "TOTAL_BUYRIO": 0.024792061383,
    }, 2_959_500_000, "buy", 0)
    hot_money = astock._branch_row({
        "OPERATEDEPT_NAME": "国盛证券股份有限公司宁波桑田路证券营业部",
        "BUY": 1_000_000, "SELL": 39_595_742, "NET": -38_595_742,
        "TOTAL_SELLRIO": 0.013379210972,
    }, 2_959_500_000, "sell", 0)

    assert quant["ratio"] == 2.48
    assert quant["category"] == "quant"
    assert quant["tags"] == ["量化基金"]
    assert hot_money["category"] == "hot_money"
    assert hot_money["tags"] == ["游资", "宁波桑田路"]


def test_daily_dragon_tiger_keeps_five_official_seats_with_same_display_name(monkeypatch):
    import server
    from duanxian import theme_reasons

    astock = _route(server).endpoint.__globals__["astock"]
    detail = {
        "TRADE_DATE": "2026-10-09", "TRADE_ID": "trade-1",
        "SECURITY_CODE": "000011", "SECURITY_NAME_ABBR": "深物业A",
        "CLOSE_PRICE": 12.3, "CHANGE_RATE": 15.0, "TURNOVERRATE": 21.2,
        "BILLBOARD_DEAL_AMT": 300_000_000, "BILLBOARD_BUY_AMT": 180_000_000,
        "BILLBOARD_SELL_AMT": 120_000_000, "BILLBOARD_NET_AMT": 60_000_000,
        "EXPLANATION": "日振幅值达到15%的前5只证券",
    }
    buy_names = ["深股通专用", "机构专用", "机构专用", "机构专用", "东方财富证券拉萨团结路营业部"]
    buy_rows = [{
        "TRADE_ID": "trade-1", "SECURITY_CODE": "000011", "OPERATEDEPT_NAME": name,
        "OPERATEDEPT_CODE": "0" if name == "机构专用" else str(index),
        "BUY": 50_000_000 - index * 5_000_000, "SELL": 1_000_000,
        "NET": 49_000_000 - index * 5_000_000, "TOTAL_BUYRIO": 0.05 - index * 0.005,
    } for index, name in enumerate(buy_names)]
    sell_rows = [{
        "TRADE_ID": "trade-1", "SECURITY_CODE": "000011", "OPERATEDEPT_NAME": f"卖方营业部{index}",
        "OPERATEDEPT_CODE": str(index), "BUY": 0, "SELL": 40_000_000 - index * 4_000_000,
        "NET": -40_000_000 + index * 4_000_000, "TOTAL_SELLRIO": 0.04 - index * 0.004,
    } for index in range(5)]

    def datacenter(report_name, **kwargs):
        return {
            "RPT_DAILYBILLBOARD_DETAILSNEW": [detail],
            "RPT_BILLBOARD_DAILYDETAILSBUY": buy_rows,
            "RPT_BILLBOARD_DAILYDETAILSSELL": sell_rows,
        }[report_name]

    monkeypatch.setattr(astock, "eastmoney_datacenter", datacenter)
    monkeypatch.setattr(astock, "em_zt_topic_pool", lambda *args, **kwargs: [])
    monkeypatch.setattr(theme_reasons, "get_reasons", lambda day: ({}, None))

    stock = astock.daily_dragon_tiger("2026-10-09")["stocks"][0]

    assert len(stock["buy_branches"]) == 5
    assert len(stock["sell_branches"]) == 5
    assert [row["name"] for row in stock["buy_branches"]].count("机构专用") == 3
    assert len({row["seat_id"] for row in stock["buy_branches"]}) == 5


def test_dragon_tiger_board_route_defaults_to_latest_closed_session(monkeypatch):
    import server
    from duanxian import trade_calendar

    route = _route(server)
    globals_ = route.endpoint.__globals__
    globals_["_DC_CACHE"].clear()
    monkeypatch.setattr(trade_calendar, "latest_session", lambda: "2026-10-09")
    monkeypatch.setattr(
        globals_["astock"], "daily_dragon_tiger",
        lambda day: {"available": True, "date": day, "stocks": [], "total_records": 0, "total_stocks": 0},
    )

    response = TestClient(server.app, base_url="http://127.0.0.1").get(
        "/api/market/dragon-tiger-board"
    )

    assert response.status_code == 200
    assert response.json()["data"]["date"] == "2026-10-09"


def test_dragon_tiger_board_route_rejects_bad_date():
    import server

    response = TestClient(server.app, base_url="http://127.0.0.1").get(
        "/api/market/dragon-tiger-board?date=2026-13-40"
    )

    assert response.status_code == 400


def test_dragon_tiger_board_initializes_picker_before_loading_board():
    """龙虎榜很慢时，日期框也必须先显示最近收盘日，不能一直空白。"""
    from pathlib import Path

    source = Path("frontend/src/pages/DragonTigerBoard.tsx").read_text(encoding="utf-8")
    assert "api.marketSession()" in source
    assert "latest_closed" in source
    assert "dateReady" in source
