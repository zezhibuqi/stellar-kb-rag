"""增强模式接入测试（设计文档 6.2 / 7.5）：模式校验、stage 事件、trace 落库。"""

import json
from types import SimpleNamespace

import pytest

import llm
import order_qa
import orchestrator
from app import create_app
from config import Config


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _login(client, username="admin", password="123456"):
    resp = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200
    return resp.get_json()["token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _new_conversation(client, token) -> int:
    return client.post("/api/conversations", headers=_headers(token)).get_json()["id"]


def _plan() -> dict:
    return {
        "needs_decomposition": True,
        "intent": "knowledge",
        "filters": {},
        "aggregation": None,
        "sub_questions": [
            {
                "id": 1,
                "query": "2025 动力电池系统 营收",
                "source": "knowledge",
                "filters": {},
                "aggregation": None,
            }
        ],
        "truncated": 0,
        "fallback": False,
    }


def _sub_results() -> list[dict]:
    return [
        {
            "id": 1,
            "query": "2025 动力电池系统 营收",
            "source": "knowledge",
            "answer": "1688 万元",
            "coverage": "sufficient",
            "evidence_ids": [1],
            "key_entities": [],
            "evidence": [
                {
                    "doc_id": 1,
                    "filename": "2025年度报告.md",
                    "domain": "finance",
                    "chunk_id": 0,
                    "chunk_type": "table",
                    "start_line": 3,
                    "parent_start_line": 3,
                    "parent_end_line": 5,
                    "text": "| 营收 | 1688 |",
                }
            ],
            "order": None,
            "error": None,
        }
    ]


def test_enhanced_requires_capable_model(monkeypatch, client):
    monkeypatch.setattr(
        llm, "get_active_provider", lambda: SimpleNamespace(agent_capable=False)
    )
    token = _login(client)
    conversation_id = _new_conversation(client, token)
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={
            "conversation_id": conversation_id,
            "mode": "enhanced",
            "stream": True,
            "question": "问题",
        },
    )
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "MODE_UNAVAILABLE"


def test_enhanced_requires_stream(monkeypatch, client):
    monkeypatch.setattr(
        llm, "get_active_provider", lambda: SimpleNamespace(agent_capable=True)
    )
    token = _login(client)
    conversation_id = _new_conversation(client, token)
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={
            "conversation_id": conversation_id,
            "mode": "enhanced",
            "stream": False,
            "question": "问题",
        },
    )
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "MODE_REQUIRES_STREAM"


def test_unknown_mode_rejected(client):
    token = _login(client)
    conversation_id = _new_conversation(client, token)
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={
            "conversation_id": conversation_id,
            "mode": "turbo",
            "stream": True,
            "question": "问题",
        },
    )
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "MODE_UNAVAILABLE"


def test_enhanced_stream_emits_stage_events_and_persists_trace(monkeypatch, client):
    monkeypatch.setattr(
        llm, "get_active_provider", lambda: SimpleNamespace(agent_capable=True)
    )
    monkeypatch.setattr(orchestrator, "plan_question", lambda q, history=None: _plan())
    monkeypatch.setattr(orchestrator, "answer_sub_questions", lambda *a, **k: _sub_results())
    monkeypatch.setattr(orchestrator.llm, "stream", lambda *a, **k: iter(["答", "案"]))

    token = _login(client)
    conversation_id = _new_conversation(client, token)
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={
            "conversation_id": conversation_id,
            "mode": "enhanced",
            "stream": True,
            "question": "2025 年动力电池系统的营收是多少？",
        },
    )
    body = resp.get_data(as_text=True)
    assert '"stage": "planning"' in body
    assert '"stage": "planned"' in body
    assert '"stage": "sub_answer"' in body
    assert '"stage": "synthesizing"' in body
    assert 'data: {"token": "答"}' in body
    assert '"sub_question_id": 1' in body

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages", headers=_headers(token)
    ).get_json()
    assistant = messages[1]
    assert assistant["mode"] == "enhanced"
    assert assistant["status"] == "completed"
    assert assistant["content"] == "答案"
    assert assistant["trace"]["rounds"] == 1
    assert assistant["trace"]["sub_questions"][0]["query"] == "2025 动力电池系统 营收"
    assert assistant["sources"][0]["sub_question_id"] == 1


def test_planner_fallback_prefixes_and_keeps_standard_answer(monkeypatch, client):
    monkeypatch.setattr(
        llm, "get_active_provider", lambda: SimpleNamespace(agent_capable=True)
    )
    monkeypatch.setattr(
        orchestrator,
        "plan_question",
        lambda q, history=None: {
            "needs_decomposition": False,
            "intent": "knowledge",
            "filters": {},
            "aggregation": None,
            "sub_questions": [],
            "truncated": 0,
            "fallback": True,
        },
    )
    # 回退走的是 orchestrator 内部对 rag.answer_question 的调用
    monkeypatch.setattr(
        orchestrator.rag,
        "answer_question",
        lambda *a, **k: iter(
            ['{"token": "标准回答"}', '{"done": true, "sources": []}']
        ),
    )

    token = _login(client)
    conversation_id = _new_conversation(client, token)
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={
            "conversation_id": conversation_id,
            "mode": "enhanced",
            "stream": True,
            "question": "问题",
        },
    )
    body = resp.get_data(as_text=True)
    assert orchestrator.PLANNER_FALLBACK_PREFIX in body
    assert "标准回答" in body

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages", headers=_headers(token)
    ).get_json()
    assert messages[1]["trace"]["fallback"] is True


def test_chain_triggers_round_two_even_when_round_one_succeeds(monkeypatch, client):
    """链式问题的第二跳由 depends_on 占位符驱动，与第一轮覆盖是否充分无关。"""
    monkeypatch.setattr(
        llm, "get_active_provider", lambda: SimpleNamespace(agent_capable=True)
    )
    plan = {
        "needs_decomposition": True,
        "intent": "knowledge",
        "filters": {},
        "aggregation": None,
        "sub_questions": [
            {
                "id": 1,
                "query": "SC-500 工商业储能一体柜 配套 电芯 型号",
                "source": "knowledge",
                "filters": {},
                "aggregation": None,
                "depends_on": None,
            },
            {
                "id": 2,
                "query": "{1} 单体质量能量密度 25℃ 循环寿命",
                "source": "knowledge",
                "filters": {},
                "aggregation": None,
                "depends_on": 1,
            },
        ],
        "truncated": 0,
        "fallback": False,
    }
    monkeypatch.setattr(orchestrator, "plan_question", lambda q, history=None: plan)
    monkeypatch.setattr(
        orchestrator,
        "answer_sub_questions",
        lambda sub_plan, *a, **k: [
            {
                "id": item["id"],
                "query": item["query"],
                "source": item["source"],
                "pool": item.get("pool"),
                "answer": "SC-300",
                "coverage": "sufficient",
                "evidence_ids": [],
                "key_entities": ["SC-300"],
                "evidence": [],
                "retrieval": [],
                "order": None,
                "error": None,
            }
            for item in sub_plan["sub_questions"]
        ],
    )
    monkeypatch.setattr(orchestrator.llm, "stream", lambda *a, **k: iter(["答"]))

    token = _login(client)
    conversation_id = _new_conversation(client, token)
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={
            "conversation_id": conversation_id,
            "mode": "enhanced",
            "stream": True,
            "question": "为 SC-500 供电的那款电芯，它的单体质量能量密度和 25℃ 循环寿命分别是多少？",
        },
    )
    body = resp.get_data(as_text=True)
    assert '"follow_up": true' in body

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages", headers=_headers(token)
    ).get_json()
    trace = messages[1]["trace"]
    assert trace["rounds"] == 2
    assert trace["round_two_sub_questions"] == 1
    assert (
        trace["sub_questions"][1]["query"]
        == "SC-300 单体质量能量密度 25℃ 循环寿命"
    )
    assert trace["budgets"]["chain_limit"] > 0
    assert trace["sub_questions"][1]["pool"] == "chain"


# ── 编排层失败语义与预算（设计文档 2.6 / 7.8）─────────────────────────────


def _consume(monkeypatch, plan, sub_results, *, question="问题", role="admin",
             stream_impl=None, trace=None):
    """直接消费 run_enhanced 事件流（不经 HTTP），用于失败分支测试。"""
    monkeypatch.setattr(orchestrator, "plan_question", lambda q, history=None: plan)
    monkeypatch.setattr(
        orchestrator,
        "answer_sub_questions",
        lambda *args, **kwargs: [dict(item) for item in sub_results],
    )
    if stream_impl is not None:
        monkeypatch.setattr(orchestrator.llm, "stream", stream_impl)
    events = [json.loads(event) for event in orchestrator.run_enhanced(
        question, role, None, trace
    )]
    return events


def _order_denied_plan():
    return {
        "needs_decomposition": True,
        "intent": "order",
        "filters": {},
        "aggregation": None,
        "sub_questions": [
            {
                "id": 1,
                "query": "DD20260315004",
                "source": "order",
                "filters": {"order_no": "DD20260315004"},
                "aggregation": None,
                "depends_on": None,
            }
        ],
        "truncated": 0,
        "fallback": False,
    }


def test_all_order_denied_returns_fixed_answer_without_llm(monkeypatch):
    """越权订单：不调用任何 LLM，直接给固定话术（设计文档 2.6 安全约定）。"""
    plan = _order_denied_plan()
    denied = orchestrator._base_result(plan["sub_questions"][0])
    denied["order"] = {
        "status": "denied",
        "rows": [],
        "aggregation": None,
        "truncated": False,
    }

    def forbidden(*args, **kwargs):
        raise AssertionError("全部订单子问题被拒绝时不得调用 LLM")

    trace: dict = {}
    events = _consume(
        monkeypatch, plan, [denied], role="employee", stream_impl=forbidden, trace=trace
    )
    text = "".join(event["token"] for event in events if "token" in event)
    assert text == order_qa.ORDER_FORBIDDEN_ANSWER
    assert [event for event in events if event.get("done")][0]["sources"] == []
    assert trace["fallback_reason"] == "order_denied"


def test_all_sub_answers_failed_falls_back_to_standard(monkeypatch):
    """全部子问题作答失败：退回标准模式单跳一次，并显式说明（设计文档 2.6）。"""
    plan = _plan()
    failed = orchestrator._base_result(plan["sub_questions"][0])
    failed["error"] = "上游失败"
    monkeypatch.setattr(
        orchestrator.rag,
        "answer_question",
        lambda *args, **kwargs: iter(
            ['{"token": "标准回答"}', '{"done": true, "sources": []}']
        ),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("全部子答案失败时不应进入合成调用")

    trace: dict = {}
    events = _consume(
        monkeypatch, plan, [failed], stream_impl=forbidden, trace=trace
    )
    text = "".join(event["token"] for event in events if "token" in event)
    assert orchestrator.SUB_ANSWER_FALLBACK_PREFIX in text
    assert "标准回答" in text
    assert trace["fallback"] is True
    assert trace["fallback_reason"] == "sub_answers_failed"


def test_decomposition_without_sub_questions_falls_back(monkeypatch):
    """规划说要拆解却给出空列表：按规划失败退回标准模式，不得带空证据合成。"""
    plan = {
        "needs_decomposition": True,
        "intent": "knowledge",
        "filters": {},
        "aggregation": None,
        "sub_questions": [],
        "truncated": 0,
        "fallback": False,
    }
    monkeypatch.setattr(
        orchestrator.rag,
        "answer_question",
        lambda *args, **kwargs: iter(['{"token": "标准回答"}']),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("空子问题列表不得进入合成调用")

    trace: dict = {}
    events = _consume(monkeypatch, plan, [], stream_impl=forbidden, trace=trace)
    text = "".join(event["token"] for event in events if "token" in event)
    assert orchestrator.PLANNER_FALLBACK_PREFIX in text
    assert "标准回答" in text
    assert trace["fallback_reason"] == "planner_failed"


def test_total_budget_exhausted_skips_round_two(monkeypatch):
    """第一轮耗尽整轮预算：跳过补充检索、缺项显式落库（AGENT_TOTAL_BUDGET）。"""
    clock = {"t": 1000.0}
    monkeypatch.setattr(orchestrator.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(Config, "AGENT_TOTAL_BUDGET", 100)
    plan = {
        "needs_decomposition": True,
        "intent": "knowledge",
        "filters": {},
        "aggregation": None,
        "sub_questions": [
            {
                "id": 1,
                "query": "SC-500 配套电芯 型号",
                "source": "knowledge",
                "filters": {},
                "aggregation": None,
                "depends_on": None,
            },
            {
                "id": 2,
                "query": "{1} 循环寿命",
                "source": "knowledge",
                "filters": {},
                "aggregation": None,
                "depends_on": 1,
            },
        ],
        "truncated": 0,
        "fallback": False,
    }

    def answer(*args, **kwargs):
        clock["t"] = 2000.0  # 第一轮把 100s 预算耗尽
        item = orchestrator._base_result(plan["sub_questions"][0])
        item["answer"] = "SC-300"
        item["coverage"] = "sufficient"
        item["key_entities"] = ["SC-300"]
        return [item]

    monkeypatch.setattr(orchestrator, "plan_question", lambda q, history=None: plan)
    monkeypatch.setattr(orchestrator, "answer_sub_questions", answer)
    monkeypatch.setattr(orchestrator.llm, "stream", lambda *a, **k: iter(["答"]))

    trace: dict = {}
    events = [
        json.loads(event)
        for event in orchestrator.run_enhanced("问题", "admin", None, trace)
    ]
    follow_ups = [
        event
        for event in events
        if event.get("stage") == "sub_answer" and event.get("follow_up")
    ]
    assert follow_ups and follow_ups[0]["error"] == "超出整轮时间预算，未执行补充检索"
    assert trace["budget_exhausted"] is True
    assert trace["round_two_sub_questions"] == 1
    assert trace["timings"]["total_budget_s"] == 100


def test_trace_records_model_timings_and_usage(monkeypatch):
    """trace 补齐模型 id、每步耗时与 token 用量（设计文档 7.8）。"""
    plan = _plan()
    plan["usage"] = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    item = _sub_results()[0]
    item["usage"] = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    item["elapsed_ms"] = 1234

    def fake_stream(prompt, **kwargs):
        kwargs["usage"].update(
            {"prompt_tokens": 500, "completion_tokens": 50, "total_tokens": 550}
        )
        return iter(["答"])

    trace: dict = {}
    _consume(monkeypatch, plan, [item], stream_impl=fake_stream, trace=trace)

    assert trace["model"]["id"] == llm.get_active_provider().id
    assert trace["model"]["model"]
    assert trace["timings"]["plan_ms"] >= 0
    assert trace["timings"]["synthesis_ms"] >= 0
    assert trace["usage"]["plan"]["total_tokens"] == 12
    assert trace["usage"]["synthesis"]["total_tokens"] == 550
    assert trace["usage"]["sub_answers"][0]["usage"]["total_tokens"] == 120
    assert trace["sub_questions"][0]["elapsed_ms"] == 1234
