"""订单结构化问答：意图路由 + 参数化 SQL 模板（功能设计文档 V1.0）。"""

import json
import logging
import re
from datetime import date, datetime, timedelta

import llm
from models import get_connection

logger = logging.getLogger("order_qa")

ORDER_ALLOWED_ROLES = {"aftersale", "admin"}
ORDER_FORBIDDEN_ANSWER = "订单数据仅售后人员与管理员可查询，如有需要请联系售后部门。"
NO_RESULT_ANSWER = "未查询到符合条件的订单。"
ROUTER_FALLBACK_PREFIX = "未能识别为订单查询，按知识库回答。"
LIST_LIMIT = 20

ALLOWED_FILTERS = {
    "order_no",
    "customer_name",
    "product_type",
    "payment_method",
    "created_from",
    "created_to",
    "status",
}
ALLOWED_AGGREGATIONS = {"count", "sum", "avg"}
ALLOWED_PRODUCTS = {"SC-100", "SC-200", "SC-300", "SC-400", "SC-500"}
ALLOWED_PAYMENTS = {"支付宝", "微信支付", "银行转账", "对公转账"}
ALLOWED_STATUS = {"completed", "pending"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_ROUTE_EXAMPLES = """示例1：
用户：订单 DD20260315004 什么时候完成？
输出：{"intent": "order", "filters": {"order_no": "DD20260315004"}, "aggregation": null}

示例2：
用户：张伟客户一共有几笔订单？
输出：{"intent": "order", "filters": {"customer_name": "张伟"}, "aggregation": "count"}

示例3：
用户：6 月订单总金额是多少？
输出：{"intent": "order", "filters": {"created_from": "2026-06-01", "created_to": "2026-06-30"}, "aggregation": "sum"}

示例4：
用户：目前未完成的订单有几笔？
输出：{"intent": "order", "filters": {"status": "pending"}, "aggregation": "count"}

示例5：
用户：差旅报销流程是什么？
输出：{"intent": "knowledge", "filters": {}, "aggregation": null}

示例6：
用户：订单 DD20260315004 状态如何？破损商品的退换货流程是什么？
输出：{"intent": "mixed", "filters": {"order_no": "DD20260315004"}, "aggregation": null}

示例7：
用户：2026年3月的订单有几笔？
输出：{"intent": "order", "filters": {"created_from": "2026-03-01", "created_to": "2026-03-31"}, "aggregation": "count"}"""


def _format_history(history: list | None) -> str:
    parts = []
    for msg in history or []:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "user":
            parts.append(f"用户：{content}")
        elif role == "assistant":
            parts.append(f"助手：{content}")
    return ("\n".join(parts) + "\n") if parts else ""


def build_route_prompt(question: str, history: list | None) -> str:
    return (
        "你是企业知识系统的订单查询意图路由器。当前日期："
        f"{date.today().isoformat()}。\n"
        "判断用户问题是否涉及订单数据库查询，输出严格 JSON："
        '{"intent": "order"|"knowledge"|"mixed", "filters": {...}, '
        '"aggregation": null|"count"|"sum"|"avg"}。\n'
        "filters 允许的键：order_no（精确订单号）、customer_name（客户姓名）、"
        "product_type（SC-100~SC-500）、payment_method（支付宝/微信支付/银行转账/对公转账）、"
        "created_from/created_to（YYYY-MM-DD，含当天）、status（completed/pending）。\n"
        "只抽取问题中明确给出的条件，不得脑补；聚合问题设置 aggregation；"
        "同时涉及订单与知识库的设为 mixed；拿不准一律 knowledge。"
        "只输出 JSON，不要输出任何其他文字。\n"
        f"{_ROUTE_EXAMPLES}\n\n历史对话：\n{_format_history(history)}"
        f"用户问题：{question}"
    )


def route_question(question: str, history: list | None = None) -> dict:
    """意图路由：LLM JSON 输出，失败重试一次，仍失败回退 knowledge。"""
    prompt = build_route_prompt(question, history)
    for attempt in range(2):
        try:
            data = llm.invoke_json(prompt)
            intent = data.get("intent")
            if intent not in ("order", "knowledge", "mixed"):
                raise ValueError(f"非法 intent: {intent}")
            raw_filters = data.get("filters") or {}
            filters = {
                key: value
                for key, value in raw_filters.items()
                if key in ALLOWED_FILTERS
            }
            aggregation = data.get("aggregation")
            if aggregation is not None and aggregation not in ALLOWED_AGGREGATIONS:
                aggregation = None
            return {
                "intent": intent,
                "filters": filters,
                "aggregation": aggregation,
                "fallback": False,
            }
        except Exception as exc:  # noqa: BLE001 - 路由失败需回退
            logger.warning("意图路由解析失败（第 %s 次）：%s", attempt + 1, exc)
    return {
        "intent": "knowledge",
        "filters": {},
        "aggregation": None,
        "fallback": True,
    }


def _valid_date(value) -> date | None:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def execute_order_query(filters: dict, aggregation: str | None = None) -> dict:
    """参数化 SQL 执行；未知/非法过滤键丢弃并记日志。"""
    where: list[str] = []
    params: list = []

    for key, value in (filters or {}).items():
        if key == "order_no":
            if isinstance(value, str) and value:
                where.append("order_no = ?")
                params.append(value)
        elif key == "customer_name":
            if isinstance(value, str) and value.strip():
                where.append("customer_name LIKE ?")
                params.append(f"%{value.strip()}%")
        elif key == "product_type":
            if value in ALLOWED_PRODUCTS:
                where.append("product_type = ?")
                params.append(value)
            else:
                logger.warning("丢弃非法 product_type：%s", value)
        elif key == "payment_method":
            if value in ALLOWED_PAYMENTS:
                where.append("payment_method = ?")
                params.append(value)
            else:
                logger.warning("丢弃非法 payment_method：%s", value)
        elif key == "created_from":
            day = _valid_date(value)
            if day:
                where.append("created_at >= ?")
                params.append(day.strftime("%Y-%m-%d 00:00:00"))
            else:
                logger.warning("丢弃非法 created_from：%s", value)
        elif key == "created_to":
            day = _valid_date(value)
            if day:
                where.append("created_at < ?")
                params.append((day + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00"))
            else:
                logger.warning("丢弃非法 created_to：%s", value)
        elif key == "status":
            if value in ALLOWED_STATUS:
                if value == "completed":
                    where.append("completed_at IS NOT NULL")
                else:
                    where.append("completed_at IS NULL")
            else:
                logger.warning("丢弃非法 status：%s", value)
        else:
            logger.warning("丢弃未知过滤键：%s", key)

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    conn = get_connection()

    if aggregation in ALLOWED_AGGREGATIONS:
        if aggregation == "count":
            sql = f"SELECT COUNT(*) AS value FROM orders{where_sql}"
        elif aggregation == "sum":
            sql = f"SELECT ROUND(SUM(total_amount), 2) AS value FROM orders{where_sql}"
        else:
            sql = f"SELECT ROUND(AVG(total_amount), 2) AS value FROM orders{where_sql}"
        row = conn.execute(sql, params).fetchone()
        return {
            "rows": [],
            "aggregation": {"type": aggregation, "value": row["value"]},
            "truncated": False,
        }

    sql = (
        "SELECT order_no, customer_name, contact, product_type, quantity, "
        "created_at, completed_at, payment_method, total_amount FROM orders"
        f"{where_sql} ORDER BY created_at DESC, id DESC LIMIT ?"
    )
    rows = [
        dict(row)
        for row in conn.execute(sql, params + [LIST_LIMIT + 1]).fetchall()
    ]
    truncated = len(rows) > LIST_LIMIT
    return {"rows": rows[:LIST_LIMIT], "aggregation": None, "truncated": truncated}


def mask_contact(contact) -> str:
    if contact and len(contact) == 11 and contact.isdigit():
        return f"{contact[:3]}****{contact[-4:]}"
    return contact or ""


def format_order_context(result: dict) -> str:
    """将查询结果格式化为注入 LLM 的 Markdown 上下文（contact 已脱敏）。"""
    aggregation = result.get("aggregation")
    if aggregation:
        label = {
            "count": "订单数量",
            "sum": "总金额(元)",
            "avg": "平均金额(元)",
        }[aggregation["type"]]
        return f"【订单数据库查询结果】聚合统计：{label} = {aggregation['value']}"

    rows = result.get("rows") or []
    if not rows:
        return "【订单数据库查询结果】命中 0 条订单。"
    head = "【订单数据库查询结果】"
    if result.get("truncated"):
        head += "（命中超过 20 条，仅展示最近 20 条）"
    else:
        head += f"（共 {len(rows)} 条）"
    lines = [
        head,
        "| 订单号 | 客户 | 联系方式 | 产品型号 | 数量 | 创建时间 | 完成时间 | 支付方式 | 总金额(元) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        completed = row["completed_at"] or "未完成"
        lines.append(
            f"| {row['order_no']} | {row['customer_name']} | "
            f"{mask_contact(row['contact'])} | {row['product_type']} | "
            f"{row['quantity']} | {row['created_at']} | {completed} | "
            f"{row['payment_method']} | {row['total_amount']:.2f} |"
        )
    return "\n".join(lines)


def build_database_source(result: dict) -> dict:
    """构造数据库来源标记（sources 追加项）。"""
    aggregation = result.get("aggregation")
    if aggregation:
        preview = f"聚合结果：{aggregation['type']}={aggregation['value']}"
    else:
        rows = result.get("rows") or []
        if not rows:
            preview = "命中 0 条订单"
        else:
            order_nos = "、".join(row["order_no"] for row in rows[:5])
            preview = f"命中 {len(rows)} 条订单：{order_nos}"
            if result.get("truncated") or len(rows) > 5:
                preview += "…"
    return {
        "source_type": "database",
        "filename": "订单数据库（SQLite）",
        "domain": "aftersale",
        "content_preview": preview,
        "doc_id": None,
        "chunk_id": None,
        "chunk_type": None,
        "start_line": None,
    }


def prepare_order_context(route: dict, user_role: str) -> dict:
    """权限校验 + 查询执行；DB 异常按无结果处理。"""
    if user_role not in ORDER_ALLOWED_ROLES:
        return {"authorized": False}
    try:
        result = execute_order_query(
            route.get("filters") or {}, route.get("aggregation")
        )
    except Exception:  # noqa: BLE001 - 订单查询异常不阻塞回答
        logger.exception("订单查询执行异常，按无结果处理")
        result = {"rows": [], "aggregation": None, "truncated": False}
    return {"authorized": True, "result": result}
