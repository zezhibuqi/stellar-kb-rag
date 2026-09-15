"""Stage O1 订单数据层测试：Schema、种子数量/幂等、金额与状态推导。"""

from orders_seed import PRODUCT_PRICES, build_orders
from models import get_connection


def test_orders_schema():
    """orders 表字段与设计文档一致（多字段/少字段都算失败）。"""
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
    """种子约 50 条且可重复执行：再次 seed_orders() 不重复插入。"""
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    assert 45 <= count <= 55

    from orders_seed import seed_orders

    seed_orders()
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == count


def test_orders_amount_and_status_derivation():
    """逐条核对金额 = 单价 × 数量、完成时间与状态推导，并留出未完成订单的合理区间。"""
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
