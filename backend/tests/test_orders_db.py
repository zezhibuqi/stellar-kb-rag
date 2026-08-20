"""Stage O1 订单数据层测试：Schema、种子数量/幂等、金额与状态推导。"""

from orders_seed import PRODUCT_PRICES, build_orders
from models import get_connection


def test_orders_schema():
    conn = get_connection()
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(orders)")}
    assert columns == {
        "id",
        "order_no",
        "customer_name",
        "contact",
        "product_type",
        "quantity",
        "created_at",
        "completed_at",
        "payment_method",
        "total_amount",
    }


def test_orders_seeded_and_idempotent():
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    assert 45 <= count <= 55

    from orders_seed import seed_orders

    seed_orders()
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == count


def test_orders_amount_and_status_derivation():
    conn = get_connection()
    rows = build_orders()
    for order in rows:
        row = conn.execute(
            "SELECT product_type, quantity, completed_at, total_amount "
            "FROM orders WHERE order_no = ?",
            (order["order_no"],),
        ).fetchone()
        assert row is not None
        assert row["total_amount"] == (
            PRODUCT_PRICES[row["product_type"]] * row["quantity"]
        )
        assert (row["completed_at"] is None) == (order["completed_at"] is None)

    pending = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE completed_at IS NULL"
    ).fetchone()[0]
    assert 8 <= pending <= 15


def test_order_no_unique_and_format():
    rows = build_orders()
    order_nos = [order["order_no"] for order in rows]
    assert len(order_nos) == len(set(order_nos))
    assert all(no.startswith("DD") and len(no) == 13 for no in order_nos)
