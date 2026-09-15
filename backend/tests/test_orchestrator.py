"""增强模式规划步测试（设计文档 7.8）：契约解析、清洗、截断与回退。"""

import orchestrator
from config import Config


def _plan(monkeypatch, payload):
    """把 llm.invoke_json 固定返回 payload，然后跑一次 plan_question。"""
    monkeypatch.setattr(orchestrator.llm, "invoke_json", lambda *a, **k: payload)
    return orchestrator.plan_question("问题")


def test_plan_parses_sub_questions(monkeypatch):
    """规划解析：id 由编排器重新编号（不采信模型编号），过滤条件保留。"""
    plan = _plan(
        monkeypatch,
        {
            "needs_decomposition": True,
            "intent": "knowledge",
            "sub_questions": [
                {"id": 9, "query": "2025 动力电池系统 营收", "source": "knowledge"},
                {"query": "DD20260315004", "source": "order", "filters": {"order_no": "DD20260315004"}},
            ],
        },
    )
    assert plan["fallback"] is False
    assert plan["needs_decomposition"] is True
    # id 由编排器重新编号，不信模型给的编号
    assert [item["id"] for item in plan["sub_questions"]] == [1, 2]
    assert plan["sub_questions"][1]["source"] == "order"
    assert plan["sub_questions"][1]["filters"] == {"order_no": "DD20260315004"}


def test_plan_drops_unknown_filters_and_bad_values(monkeypatch):
    """未知过滤键（drop_table）与非法聚合（delete_all）都被丢弃，合法条件保留。"""
    plan = _plan(
        monkeypatch,
        {
            "intent": "order",
            "filters": {"status": "pending", "drop_table": "orders", "created_from": "2026-03-01"},
            "aggregation": "delete_all",
        },
    )
    assert plan["filters"] == {"status": "pending", "created_from": "2026-03-01"}
    assert plan["aggregation"] is None


def test_plan_defaults_unknown_source_to_knowledge(monkeypatch):
    """子问题 source 非法（sql）时降级为 knowledge，避免走到未授权的工具。"""
    plan = _plan(
        monkeypatch,
        {"intent": "knowledge", "sub_questions": [{"query": "q", "source": "sql"}]},
    )
    assert plan["sub_questions"][0]["source"] == "knowledge"


def test_plan_truncates_beyond_execution_limit(monkeypatch):
    """超过子问题执行上限的部分被截断，并如实记录 truncated 数量。"""
    total = Config.AGENT_MAX_SUB_QUESTIONS + 3
    plan = _plan(
        monkeypatch,
        {
            "intent": "knowledge",
            "sub_questions": [{"query": f"q{index}"} for index in range(total)],
        },
    )
    assert len(plan["sub_questions"]) == Config.AGENT_MAX_SUB_QUESTIONS
    assert plan["truncated"] == 3


def test_plan_rejects_illegal_intent_then_falls_back(monkeypatch):
    """非法 intent 触发重试一次，仍失败则返回 fallback（不抛异常中断问答）。"""
    calls = {"n": 0}

    def bad(*args, **kwargs):
        """每次调用都返回非法 intent，用于验证重试上限。"""
        calls["n"] += 1
        return {"intent": "database"}

    monkeypatch.setattr(orchestrator.llm, "invoke_json", bad)
    plan = orchestrator.plan_question("问题")
    assert calls["n"] == 2, "解析失败应重试一次"
    assert plan["fallback"] is True
    assert plan["intent"] == "knowledge"
    assert plan["sub_questions"] == []


def test_plan_single_hop_keeps_fields(monkeypatch):
    """单跳判定：needs_decomposition=False 时意图/过滤/聚合仍要完整保留（驱动标准链路）。"""
    plan = _plan(
        monkeypatch,
        {
            "needs_decomposition": False,
            "intent": "order",
            "filters": {"status": "pending"},
            "aggregation": "count",
            "sub_questions": [],
        },
    )
    assert plan["needs_decomposition"] is False
    assert plan["intent"] == "order"
    assert plan["aggregation"] == "count"


def test_plan_keeps_depends_on_for_chain(monkeypatch):
    """链式依赖被保留：第二跳的 depends_on 指向第一跳，供后续填占位符。"""
    plan = _plan(
        monkeypatch,
        {
            "intent": "knowledge",
            "sub_questions": [
                {"id": 1, "query": "SC-500 配套 电芯 型号", "source": "knowledge"},
                {
                    "id": 2,
                    "query": "{1} 单体质量能量密度",
                    "source": "knowledge",
                    "depends_on": 1,
                },
            ],
        },
    )
    assert plan["sub_questions"][1]["depends_on"] == 1


def test_plan_drops_unknown_dependency(monkeypatch):
    """依赖指向不存在的子问题时被清空（防止编排器索引越界或死等）。"""
    plan = _plan(
        monkeypatch,
        {
            "intent": "knowledge",
            "sub_questions": [{"id": 1, "query": "q", "depends_on": 7}],
        },
    )
    assert plan["sub_questions"][0]["depends_on"] is None


def test_plan_requests_its_own_token_budget(monkeypatch):
    """规划不能沿用提供方的 router_max_tokens（默认 300），否则推理会吃光预算。"""
    captured = {}

    def fake_invoke_json(prompt, **kwargs):
        """记录调用参数，用于断言规划请求了自己的 token 预算。"""
        captured.update(kwargs)
        return {"intent": "knowledge"}

    monkeypatch.setattr(orchestrator.llm, "invoke_json", fake_invoke_json)
    orchestrator.plan_question("问题")
    assert captured["max_tokens"] == Config.AGENT_PLAN_MAX_TOKENS
    assert Config.AGENT_PLAN_MAX_TOKENS >= 8000, "预算需显著高于实测推理用量"
