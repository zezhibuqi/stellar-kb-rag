"""增强模式接入测试（设计文档 6.2 / 7.5）：模式校验、stage 事件、trace 落库。"""

from types import SimpleNamespace

import pytest

import llm
import orchestrator
from app import create_app


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
