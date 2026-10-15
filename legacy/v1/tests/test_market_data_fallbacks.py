import json

import net_utils
from duanxian import breadth
from duanxian import fetchers
from duanxian import stock_explorer as explorer


def _complete_sina_distribution():
    return {
        "universe": 5568,
        "up": 1098,
        "down": 4328,
        "flat": 142,
        "deep_up_5_incl": 121,
        "deep_down_5": 241,
        "source": "sina_market_center",
        "requests": 13,
    }


def test_market_network_diagnostic_covers_the_actual_eastmoney_width_endpoint():
    width = next(item for item in net_utils._DIAGNOSTIC_SOURCES if item[0] == "eastmoney_width")

    assert width[1] == "东财市场宽度"
    assert "/api/qt/ulist.np/get" in width[2]
    assert "f104,f105,f106" in width[2]


def test_clist_falls_back_to_official_backup_when_clash_resets_primary_hosts(monkeypatch):
    calls = []

    class Response:
        def __init__(self, page):
            self.page = page

        def json(self):
            return {
                "data": {
                    "total": 2,
                    "diff": [{
                        "f12": "601811" if self.page == 1 else "300300",
                        "f14": "新华文轩" if self.page == 1 else "海峡创新",
                        "f3": 2.25 if self.page == 1 else 19.98,
                    }],
                },
            }

    def fake_get(url, **_kwargs):
        calls.append(url)
        if "push2test.eastmoney.com" not in url:
            raise ConnectionError("Clash closed the HTTPS connection")
        return Response(_kwargs["params"]["pn"])

    monkeypatch.setattr(fetchers, "_direct_get", fake_get)
    monkeypatch.setattr(fetchers.time, "sleep", lambda _seconds: None)

    rows = fetchers._clist(
        "b:BK0867", "f3", "f12,f14,f3", pz=1, max_pages=2, retries=1,
    )

    assert [row["f12"] for row in rows] == ["601811", "300300"]
    assert calls == [
        "https://push2delay.eastmoney.com/api/qt/clist/get",
        "https://push2.eastmoney.com/api/qt/clist/get",
        "https://push2test.eastmoney.com/api/qt/clist/get",
        "https://push2test.eastmoney.com/api/qt/clist/get",
    ]


def test_breadth_uses_independent_distribution_when_eastmoney_clist_is_blocked(monkeypatch):
    monkeypatch.setattr(breadth.os.path, "isfile", lambda _path: False)
    monkeypatch.setattr(breadth.trade_calendar, "live_quotes_are_close_of", lambda _date: (True, ""))
    monkeypatch.setattr(breadth.trade_calendar, "is_settled", lambda _date: False)
    monkeypatch.setattr(breadth, "_index_breadth", lambda: {
        "up": 1098, "down": 4047, "flat": 142, "amount_yi": 16533.6,
    })
    monkeypatch.setattr(breadth, "_page", lambda _page: (_ for _ in ()).throw(ConnectionError("eastmoney reset")))
    monkeypatch.setattr(breadth, "_sina_distribution", _complete_sina_distribution)

    result = breadth.market_breadth("2026-09-24")

    assert result["available"] is True
    assert result["dist_available"] is True
    assert result["dist_partial"] is False
    assert result["universe"] == 5568
    assert result["deep_up_5_incl"] == 121
    assert result["deep_down_5"] == 241
    assert result["dist_source"] == "sina_market_center"


def test_breadth_uses_sina_and_tencent_when_eastmoney_index_width_is_blocked(monkeypatch):
    monkeypatch.setattr(breadth.os.path, "isfile", lambda _path: False)
    monkeypatch.setattr(breadth.trade_calendar, "live_quotes_are_close_of", lambda _date: (True, ""))
    monkeypatch.setattr(breadth.trade_calendar, "is_settled", lambda _date: False)
    monkeypatch.setattr(breadth, "_index_breadth", lambda: None)
    monkeypatch.setattr(breadth, "_page", lambda _page: (_ for _ in ()).throw(
        AssertionError("Eastmoney distribution must not be retried after the full independent fallback succeeds")
    ))
    monkeypatch.setattr(breadth, "_sina_distribution", _complete_sina_distribution)
    monkeypatch.setattr(breadth, "_tencent_index_amount", lambda _date: 17028.0, raising=False)

    result = breadth.market_breadth("2026-09-28")

    assert result["available"] is True
    assert result["up"] == 1098
    assert result["down"] == 4328
    assert result["flat"] == 142
    assert result["amount_yi"] == 17028.0
    assert result["up_down_source"] == "sina_market_center+tencent_indices"
    assert result["up_down_scope"] == "沪深 A 股（新浪行情中心）"
    assert result["dist_available"] is True
    assert result["dist_source"] == "sina_market_center"


def test_incomplete_latest_breadth_cache_is_retried(monkeypatch, tmp_path):
    cached = {
        "schema": breadth._SCHEMA,
        "date": "2026-09-24",
        "data": {
            "available": True, "date": "2026-09-24", "up": 1098, "down": 4047,
            "flat": 142, "amount_yi": 16533.6, "up_down_scope": "沪深两市（不含北交所）",
            "universe": 0, "deep_up_5_incl": None, "deep_down_5": None,
            "dist_scope": "全 A（含北交所）", "dist_available": False,
            "dist_partial": True, "requests": 1,
        },
    }
    path = tmp_path / "2026-09-24.json"
    path.write_text(json.dumps(cached), encoding="utf-8")
    monkeypatch.setattr(breadth, "_cache_path", lambda _date: str(path))
    monkeypatch.setattr(breadth.trade_calendar, "live_quotes_are_close_of", lambda _date: (True, ""))
    monkeypatch.setattr(breadth.trade_calendar, "is_settled", lambda _date: False)
    monkeypatch.setattr(breadth, "_index_breadth", lambda: None)
    monkeypatch.setattr(breadth, "_page", lambda _page: (_ for _ in ()).throw(ConnectionError("eastmoney reset")))
    monkeypatch.setattr(breadth, "_sina_distribution", _complete_sina_distribution)

    result = breadth.market_breadth("2026-09-24")

    assert result["dist_available"] is True
    assert result["dist_source"] == "sina_market_center"
    assert result["up"] == 1098


def test_industry_peers_uses_baostock_full_historical_classification(monkeypatch):
    explorer.reset_memory_for_tests()
    monkeypatch.setattr(explorer, "_board_catalog", lambda _kind: (_ for _ in ()).throw(ConnectionError("eastmoney reset")))
    monkeypatch.setattr(explorer, "_baostock_industry_rows", lambda _date: [
        {"updateDate": "2026-09-21", "code": "sh.600843", "code_name": "上工申贝", "industry": "C35专用设备制造业", "industryClassification": "证监会行业分类"},
        {"updateDate": "2026-09-21", "code": "sh.600031", "code_name": "三一重工", "industry": "C35专用设备制造业", "industryClassification": "证监会行业分类"},
        {"updateDate": "2026-09-21", "code": "sh.600202", "code_name": "哈空调", "industry": "C34通用设备制造业", "industryClassification": "证监会行业分类"},
    ])
    monkeypatch.setattr(explorer, "_member_quotes", lambda codes, _date: {
        "600843": {"price": 8.37, "change_pct": 9.99, "amount_wan": 44721.496, "mcap_yi": 59.7, "float_mcap_yi": 39.27},
        "600031": {"price": 18.5, "change_pct": 1.2, "amount_wan": 80000, "mcap_yi": 1500, "float_mcap_yi": 1490},
    })

    result = explorer.industry_peers("专用设备", "600843", date="2026-09-24")

    assert result["available"] is True
    assert result["degraded"] is False
    assert result["source"] == "baostock_csrc_industry"
    assert result["requested_date"] == "2026-09-24"
    assert result["classification_date"] == "2026-09-21"
    assert result["quote_date"] == "2026-09-24"
    assert result["total"] == 2
    assert [row["code"] for row in result["members"]] == ["600843", "600031"]
    assert "证监会行业分类" in result["note"]


def test_deepdive_latest_button_does_not_race_job_polling():
    source = open("frontend/src/pages/DeepDive.tsx", encoding="utf-8").read()
    marker = 'aria-label="查看最近完成报告"'
    assert marker in source
    button = source[source.index(marker):source.index(marker) + 650]
    assert "pollOnce" not in button
    assert "loadingLatest" in button
    assert "已加载最近完成报告" in source


def test_deepdive_page_exposes_immutable_report_history():
    source = open("frontend/src/pages/DeepDive.tsx", encoding="utf-8").read()

    assert 'agentRequest<DeepDiveHistory>("/deepdive/reports")' in source
    assert "历史辩论报告" in source
    assert "history.reports.map" in source
    assert "loadHistoricalReport(report)" in source


def test_deepdive_history_exposes_confirmed_report_deletion():
    source = open("frontend/src/pages/DeepDive.tsx", encoding="utf-8").read()
    assert "deleteHistoricalReport(report)" in source
    assert "确认删除这份辩论报告" in source
    assert "Trash2" in source


def test_stock_research_and_review_modal_share_the_same_concept_card():
    stock_page = open("frontend/src/pages/StockData.tsx", encoding="utf-8").read()
    review_modal = open("frontend/src/components/LadderExplorer.tsx", encoding="utf-8").read()
    shared = open("frontend/src/components/StockConceptCard.tsx", encoding="utf-8").read()

    assert 'from "@/components/StockConceptCard"' in stock_page
    assert 'from "@/components/StockConceptCard"' in review_modal
    assert "<StockConceptCard" in stock_page and "<StockConceptCard" in review_modal
    assert "板块归属 · 概念" in shared and "当下热门概念命中" in shared


def test_review_read_overlays_recovered_breadth_without_rewriting_report(monkeypatch):
    import server

    stored = {
        "target_date": "2026-09-24",
        "focus": {"verification_items": []},
        "market_facts": {"breadth": {"available": True, "dist_available": False}},
    }
    monkeypatch.setattr(server.review_store, "load", lambda _date: stored)
    monkeypatch.setattr(server.review_store, "usable", lambda _payload: True)
    monkeypatch.setattr(server.reflection, "latest_reflection", lambda end=None: None)
    monkeypatch.setattr(server.reflection, "scoreboard", lambda end=None: {})
    monkeypatch.setattr(server.breadth, "market_breadth", lambda _date: {
        "available": True, "dist_available": True, "deep_up_5_incl": 113,
        "deep_down_5": 258, "universe": 5568,
    })

    response = server.api_latest("2026-09-24")
    payload = json.loads(response.body)

    assert payload["market_facts"]["breadth"]["deep_up_5_incl"] == 113
    assert stored["market_facts"]["breadth"]["dist_available"] is False
