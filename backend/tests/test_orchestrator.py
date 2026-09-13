"""增强模式规划步测试（设计文档 7.8）：契约解析、清洗、截断与回退。"""

import orchestrator
from config import Config


def _plan(monkeypatch, payload):
    monkeypatch.setattr(orchestrator.llm, "invoke_json", lambda *a, **k: payload)
    return orchestrator.plan_question("问题")


def test_plan_parses_sub_questions(monkeypatch):
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
    plan = _plan(
        monkeypatch,
        {"intent": "knowledge", "sub_questions": [{"query": "q", "source": "sql"}]},
    )
    assert plan["sub_questions"][0]["source"] == "knowledge"


def test_plan_truncates_beyond_execution_limit(monkeypatch):
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
    calls = {"n": 0}

    def bad(*args, **kwargs):
        calls["n"] += 1
        return {"intent": "database"}

    monkeypatch.setattr(orchestrator.llm, "invoke_json", bad)
    plan = orchestrator.plan_question("问题")
    assert calls["n"] == 2, "解析失败应重试一次"
    assert plan["fallback"] is True
    assert plan["intent"] == "knowledge"
    assert plan["sub_questions"] == []


def test_plan_single_hop_keeps_fields(monkeypatch):
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
    plan = _plan(
        monkeypatch,
        {
            "intent": "knowledge",
            "sub_questions": [{"id": 1, "query": "q", "depends_on": 7}],
        },
    )
    assert plan["sub_questions"][0]["depends_on"] is None
