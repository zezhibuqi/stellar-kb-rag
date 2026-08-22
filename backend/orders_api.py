"""订单数据列表接口（仅 aftersale/admin）：过滤 + 分页 + 联系方式脱敏。"""

import logging

from flask import Blueprint, g, jsonify, request

from auth import require_auth
from errors import api_error
from models import get_connection
from order_qa import (
    ALLOWED_PAYMENTS,
    ALLOWED_PRODUCTS,
    ALLOWED_STATUS,
    ORDER_ALLOWED_ROLES,
    _valid_date,
    mask_contact,
)

logger = logging.getLogger("orders_api")

orders_bp = Blueprint("orders", __name__, url_prefix="/api/orders")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


@orders_bp.get("")
@require_auth
def list_orders():
    if g.user["role"] not in ORDER_ALLOWED_ROLES:
        return api_error("无权限查看订单数据", "FORBIDDEN", 403)

    args = request.args
    where: list[str] = []
    params: list = []

    order_no = (args.get("order_no") or "").strip()
    if order_no:
        where.append("order_no = ?")
        params.append(order_no)

    customer_name = (args.get("customer_name") or "").strip()
    if customer_name:
        where.append("customer_name LIKE ?")
        params.append(f"%{customer_name}%")

    product_type = args.get("product_type")
    if product_type in ALLOWED_PRODUCTS:
        where.append("product_type = ?")
        params.append(product_type)
    elif product_type:
        logger.warning("丢弃非法 product_type：%s", product_type)

    payment_method = args.get("payment_method")
    if payment_method in ALLOWED_PAYMENTS:
        where.append("payment_method = ?")
        params.append(payment_method)
    elif payment_method:
        logger.warning("丢弃非法 payment_method：%s", payment_method)

    status = args.get("status")
    if status in ALLOWED_STATUS:
        if status == "completed":
            where.append("completed_at IS NOT NULL")
        else:
            where.append("completed_at IS NULL")
    elif status:
        logger.warning("丢弃非法 status：%s", status)

    created_from = _valid_date(args.get("created_from"))
    if created_from:
        where.append("created_at >= ?")
        params.append(created_from.strftime("%Y-%m-%d 00:00:00"))

    created_to = _valid_date(args.get("created_to"))
    if created_to:
        from datetime import timedelta

        where.append("created_at < ?")
        params.append(
            (created_to + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
        )

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    page = _clamp_int(args.get("page", 1), 1, 1, 1_000_000_000)
    page_size = _clamp_int(
        args.get("page_size", DEFAULT_PAGE_SIZE),
        DEFAULT_PAGE_SIZE,
        1,
        MAX_PAGE_SIZE,
    )

    conn = get_connection()
    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM orders{where_sql}", params
    ).fetchone()["c"]
    rows = conn.execute(
        "SELECT order_no, customer_name, contact, product_type, quantity, "
        "created_at, completed_at, payment_method, total_amount FROM orders"
        f"{where_sql} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()

    items = [
        {
            "order_no": row["order_no"],
            "customer_name": row["customer_name"],
            "contact": mask_contact(row["contact"]),
            "product_type": row["product_type"],
            "quantity": row["quantity"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
            "payment_method": row["payment_method"],
            "total_amount": row["total_amount"],
            "status": "completed" if row["completed_at"] else "pending",
        }
        for row in rows
    ]
    return jsonify(
        {"items": items, "total": total, "page": page, "page_size": page_size}
    )
