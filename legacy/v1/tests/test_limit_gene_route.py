from fastapi.testclient import TestClient


def _route(server):
    return next(
        (route for route in server.app.routes
         if getattr(route, "path", "") == "/api/market/limit-gene"),
        None,
    )


def test_limit_gene_route_exposes_existing_calculation(monkeypatch):
    import server

    route = _route(server)
    assert route is not None, "涨停基因计算存在，但没有暴露 HTTP 读取路由"
    calls = []
    firstboard = route.endpoint.__globals__["firstboard"]
    monkeypatch.setattr(
        firstboard,
        "limit_gene",
        lambda code: calls.append(code) or {
            "available": True,
            "limit_up_count": 7,
            "stock_character_score": 63,
            "seal_success_rate": 0.7,
        },
    )

    response = TestClient(server.app, base_url="http://127.0.0.1").get(
        "/api/market/limit-gene?code=600001"
    )

    assert response.status_code == 200
    assert response.json()["data"]["limit_up_count"] == 7
    assert calls == ["600001"]


def test_limit_gene_route_rejects_invalid_code():
    import server

    response = TestClient(server.app, base_url="http://127.0.0.1").get(
        "/api/market/limit-gene?code=60001"
    )

    assert response.status_code == 400
