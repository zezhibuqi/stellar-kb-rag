"""订单数据列表接口测试：权限、过滤、分页、脱敏与状态推导。"""

import pytest

from app import create_app
from orders_seed import build_orders


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _login(client, username: str, password: str = "123456"):
    resp = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200
    return resp.get_json()["token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_unauthorized_roles_forbidden(client):
    for username in ("employee", "finance", "sales"):
        token = _login(client, username)
        resp = client.get("/api/orders", headers=_headers(token))
        assert resp.status_code == 403


def test_aftersale_and_admin_allowed(client):
    for username in ("aftersale", "admin"):
        token = _login(client, username)
        resp = client.get("/api/orders", headers=_headers(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data) == {"items", "total", "page", "page_size"}
        assert data["total"] == 50
        assert data["page"] == 1
        assert data["page_size"] == 20
        assert len(data["items"]) == 20


def test_filter_order_no_and_masking(client):
    token = _login(client, "aftersale")
    resp = client.get(
        "/api/orders?order_no=DD20260315004", headers=_headers(token)
    )
    data = resp.get_json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["order_no"] == "DD20260315004"
    assert item["contact"] == "138****5678"
    assert item["status"] == "completed"
    assert "****" in item["contact"]


def test_filter_customer_like(client):
    token = _login(client, "aftersale")
    resp = client.get("/api/orders?customer_name=张伟", headers=_headers(token))
    data = resp.get_json()
    assert data["total"] == sum(
        1 for order in build_orders() if "张伟" in order["customer_name"]
    )
    assert all("张伟" in item["customer_name"] for item in data["items"])


def test_filter_status(client):
    token = _login(client, "aftersale")
    resp = client.get("/api/orders?status=pending", headers=_headers(token))
    data = resp.get_json()
    assert data["total"] == sum(
        1 for order in build_orders() if order["completed_at"] is None
    )
    assert all(item["status"] == "pending" for item in data["items"])


def test_filter_product_payment_and_date(client):
    token = _login(client, "aftersale")
    resp = client.get(
        "/api/orders?product_type=SC-100&payment_method=银行转账",
        headers=_headers(token),
    )
    data = resp.get_json()
    assert all(
        item["product_type"] == "SC-100" and item["payment_method"] == "银行转账"
        for item in data["items"]
    )

    resp = client.get(
        "/api/orders?created_from=2026-03-01&created_to=2026-03-31",
        headers=_headers(token),
    )
    data = resp.get_json()
    assert data["total"] == 11
    assert all(
        "2026-03-01" <= item["created_at"] < "2026-04-01"
        for item in data["items"]
    )


def test_pagination(client):
    token = _login(client, "aftersale")
    resp = client.get("/api/orders?page=2&page_size=10", headers=_headers(token))
    data = resp.get_json()
    assert data["page"] == 2
    assert data["page_size"] == 10
    assert len(data["items"]) == 10

    resp = client.get("/api/orders?page_size=1000", headers=_headers(token))
    data = resp.get_json()
    assert data["page_size"] == 100
    assert data["total"] == 50

    resp = client.get("/api/orders?page=0", headers=_headers(token))
    assert resp.get_json()["page"] == 1


def test_invalid_filters_ignored(client):
    token = _login(client, "aftersale")
    resp = client.get(
        "/api/orders?status=weird&created_from=bad-date&product_type=SC-999",
        headers=_headers(token),
    )
    assert resp.status_code == 200
    assert resp.get_json()["total"] == 50
