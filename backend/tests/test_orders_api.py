"""订单数据列表接口测试：权限、过滤、分页、脱敏与状态推导。"""

import pytest

from app import create_app
from orders_seed import build_orders


@pytest.fixture()
def client():
    """独立的 Flask 测试客户端（数据由 conftest 重建）。"""
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _login(client, username: str, password: str = "123456"):
    """登录并返回 JWT。"""
    resp = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200
    return resp.get_json()["token"]


def _headers(token: str) -> dict:
    """拼装 Bearer 认证头。"""
    return {"Authorization": f"Bearer {token}"}


def test_unauthorized_roles_forbidden(client):
    """employee/finance/sales 访问订单接口一律 403（订单权限是角色白名单，不走领域表）。"""
    for username in ("employee", "finance", "sales"):
        token = _login(client, username)
        resp = client.get("/api/orders", headers=_headers(token))
        assert resp.status_code == 403


def test_aftersale_and_admin_allowed(client):
    """aftersale/admin 可访问，默认分页 20 条、总数 50。"""
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
    """按订单号精确过滤，并核对联系方式脱敏与状态字段。"""
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
    """客户名模糊匹配：总数与本地种子计算一致，返回项都含该客户名。"""
    token = _login(client, "aftersale")
    resp = client.get("/api/orders?customer_name=张伟", headers=_headers(token))
    data = resp.get_json()
    assert data["total"] == sum(
        1 for order in build_orders() if "张伟" in order["customer_name"]
    )
    assert all("张伟" in item["customer_name"] for item in data["items"])


def test_filter_status(client):
    """status=pending 只返回未完成订单，数量与种子数据一致。"""
    token = _login(client, "aftersale")
    resp = client.get("/api/orders?status=pending", headers=_headers(token))
    data = resp.get_json()
    assert data["total"] == sum(
        1 for order in build_orders() if order["completed_at"] is None
    )
    assert all(item["status"] == "pending" for item in data["items"])


def test_filter_product_payment_and_date(client):
    """多条件 AND 与日期区间（含首尾当天）过滤。"""
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
    """分页边界：page_size 上限 100、page<1 归一到 1。"""
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
    """非法枚举与非法日期被静默丢弃，等价于不加该条件（返回全集）。"""
    token = _login(client, "aftersale")
    resp = client.get(
        "/api/orders?status=weird&created_from=bad-date&product_type=SC-999",
        headers=_headers(token),
    )
    assert resp.status_code == 200
    assert resp.get_json()["total"] == 50
