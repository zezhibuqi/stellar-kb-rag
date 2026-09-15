"""Stage O2 意图路由与订单查询执行器测试（路由 LLM 一律 Mock）。"""

import pytest

import llm
import order_qa
from models import get_connection
from orders_seed import build_orders


def _mock_route(monkeypatch, data, calls=None):
    """把 llm.invoke_json 换成本地替身：data 为异常时抛出，否则原样返回（可记录提示词）。"""
    def fake_invoke_json(prompt):
        """记录提示词并按预置数据返回/抛出。"""
        if calls is not None:
            calls.append(prompt)
        if isinstance(data, Exception):
            raise data
        return data

    monkeypatch.setattr(llm, "invoke_json", fake_invoke_json)


def test_route_order_intent(monkeypatch):
    """订单意图：抽出 order_no 过滤条件且不再聚合。"""
    _mock_route(
        monkeypatch,
        {
            "intent": "order",
            "filters": {"order_no": "DD20260315004"},
            "aggregation": None,
        },
    )
    route = order_qa.route_question("订单 DD20260315004 什么时候完成？")
    assert route["intent"] == "order"
    assert route["filters"] == {"order_no": "DD20260315004"}
    assert route["aggregation"] is None
    assert route["fallback"] is False


def test_route_filters_whitelist(monkeypatch):
    """路由输出的过滤键走白名单：伪造的 evil/limit 键被丢弃，合法键保留。"""
    _mock_route(
        monkeypatch,
        {
            "intent": "order",
            "filters": {
                "customer_name": "张伟",
                "evil": "DROP TABLE orders",
                "limit": 1,
            },
            "aggregation": "count",
        },
    )
    route = order_qa.route_question("张伟有几笔订单？")
    assert route["filters"] == {"customer_name": "张伟"}
    assert route["aggregation"] == "count"


def test_route_invalid_aggregation_normalized(monkeypatch):
    """非法聚合方式（median）归一为 None，不阻断问答。"""
    _mock_route(
        monkeypatch,
        {"intent": "order", "filters": {}, "aggregation": "median"},
    )
    route = order_qa.route_question("订单平均金额？")
    assert route["aggregation"] is None


def test_route_fallback_after_two_failures(monkeypatch):
    """路由连续两次解析失败：回退 knowledge 且标记 fallback，恰好调用两次。"""
    calls = []
    _mock_route(monkeypatch, ValueError("bad json"), calls)
    route = order_qa.route_question("随便问问")
    assert route["intent"] == "knowledge"
    assert route["fallback"] is True
    assert len(calls) == 2


def test_route_fallback_on_invalid_intent(monkeypatch):
    """非法 intent 同样触发回退（不是只在抛异常时兜底）。"""
    _mock_route(monkeypatch, {"intent": "weird", "filters": {}, "aggregation": None})
    route = order_qa.route_question("随便问问")
    assert route["intent"] == "knowledge"
    assert route["fallback"] is True


def test_route_prompt_contains_current_date(monkeypatch):
    """提示词必须带当天日期，否则「6 月的订单」无法换算成绝对日期区间。"""
    calls = []
    _mock_route(
        monkeypatch,
        {"intent": "knowledge", "filters": {}, "aggregation": None},
        calls,
    )
    order_qa.route_question("今天几号")
    import datetime

    assert datetime.date.today().isoformat() in calls[0]


def test_execute_order_no_exact():
    """订单号精确匹配：命中 1 条且金额、客户正确，未触发截断。"""
    result = order_qa.execute_order_query({"order_no": "DD20260315004"})
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["order_no"] == "DD20260315004"
    assert row["customer_name"] == "张伟"
    assert row["total_amount"] == 17000.0
    assert result["truncated"] is False


def test_execute_customer_like():
    """客户名走 LIKE 模糊匹配，命中项都包含该客户名。"""
    result = order_qa.execute_order_query({"customer_name": "张伟"})
    assert result["rows"]
    assert all("张伟" in row["customer_name"] for row in result["rows"])


def test_execute_multi_filter_and():
    """多条件以 AND 连接：客户 + 支付方式的交集。"""
    result = order_qa.execute_order_query(
        {"customer_name": "张伟", "payment_method": "银行转账"}
    )
    assert result["rows"]
    assert all(
        "张伟" in row["customer_name"] and row["payment_method"] == "银行转账"
        for row in result["rows"]
    )


def test_execute_date_boundary_created_to_includes_today():
    """created_to 按「次日零点」处理，保证区间包含结束当天。"""
    result = order_qa.execute_order_query(
        {"created_from": "2026-03-01", "created_to": "2026-04-01"}
    )
    assert result["rows"]
    assert all(
        "2026-03-01" <= row["created_at"] < "2026-04-02"
        for row in result["rows"]
    )


def test_execute_status_filters():
    """status 由 completed_at 是否为空推导（没有独立的状态列）。"""
    pending = order_qa.execute_order_query({"status": "pending"})
    assert pending["rows"]
    assert all(row["completed_at"] is None for row in pending["rows"])
    completed = order_qa.execute_order_query({"status": "completed"})
    assert completed["rows"]
    assert all(row["completed_at"] is not None for row in completed["rows"])


def test_execute_aggregations():
    """三种聚合都与本地按种子计算的期望值一致（count/sum/avg，金额保留 2 位）。"""
    orders = build_orders()
    count = order_qa.execute_order_query({}, "count")
    assert count["aggregation"]["value"] == len(orders)

    expected_sum = round(sum(order["total_amount"] for order in orders), 2)
    total = order_qa.execute_order_query({}, "sum")
    assert total["aggregation"]["value"] == expected_sum

    expected_avg = round(expected_sum / len(orders), 2)
    avg = order_qa.execute_order_query({}, "avg")
    assert avg["aggregation"]["value"] == expected_avg


def test_execute_truncation():
    """列表上限 20 条：命中更多时置 truncated，并按创建时间倒序。"""
    result = order_qa.execute_order_query({})
    assert len(result["rows"]) == order_qa.LIST_LIMIT
    assert result["truncated"] is True
    created_times = [row["created_at"] for row in result["rows"]]
    assert created_times == sorted(created_times, reverse=True)


def test_execute_injection_safe():
    """注入串被当作普通字符串值绑定，不改变 SQL 语义（返回空而非全表）。"""
    result = order_qa.execute_order_query({"order_no": "1' OR '1'='1"})
    assert result["rows"] == []


def test_execute_unknown_filter_dropped():
    """未知过滤键被丢弃，不影响其余条件。"""
    result = order_qa.execute_order_query(
        {"order_no": "DD20260315004", "evil": "x"}
    )
    assert len(result["rows"]) == 1


def test_execute_invalid_date_dropped():
    """非法日期（2026-13-99）被丢弃，等价于不带时间条件。"""
    result = order_qa.execute_order_query({"created_from": "2026-13-99"})
    assert result["truncated"] is True
    assert len(result["rows"]) == order_qa.LIST_LIMIT


def test_mask_contact():
    """手机号脱敏为前 3 后 4；None 归一为空串。"""
    assert order_qa.mask_contact("13812345678") == "138****5678"
    assert order_qa.mask_contact(None) == ""


def test_format_order_context_masks_and_marks_pending():
    """注入 LLM 的订单上下文：联系方式必须脱敏，未完成订单显示「未完成」。"""
    result = {
        "rows": [
            {
                "order_no": "DD20260315004",
                "customer_name": "张伟",
                "contact": "13812345678",
                "product_type": "SC-100",
                "quantity": 20,
                "created_at": "2026-03-15 10:24:00",
                "completed_at": None,
                "payment_method": "银行转账",
                "total_amount": 17000.0,
            }
        ],
        "aggregation": None,
        "truncated": False,
    }
    text = order_qa.format_order_context(result)
    assert "138****5678" in text
    assert "未完成" in text
    assert "13812345678" not in text


def test_build_database_source():
    """数据库来源标记：固定文件名/领域、doc_id 为空，预览里带命中订单号。"""
    result = {
        "rows": [
            {"order_no": "DD20260315004"},
            {"order_no": "DD20260401002"},
        ],
        "aggregation": None,
        "truncated": False,
    }
    source = order_qa.build_database_source(result)
    assert source["source_type"] == "database"
    assert source["filename"] == "订单数据库（SQLite）"
    assert source["domain"] == "aftersale"
    assert source["doc_id"] is None
    assert "DD20260315004" in source["content_preview"]


def test_prepare_order_context_permission():
    """权限校验：非白名单角色只返回 authorized=False（不执行查询、不暴露是否存在）。"""
    route = {"filters": {"order_no": "DD20260315004"}, "aggregation": None}
    forbidden = order_qa.prepare_order_context(route, "employee")
    assert forbidden == {"authorized": False}
    allowed = order_qa.prepare_order_context(route, "aftersale")
    assert allowed["authorized"] is True
    assert len(allowed["result"]["rows"]) == 1
