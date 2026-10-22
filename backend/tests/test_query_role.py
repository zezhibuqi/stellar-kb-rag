"""管理员查询角色测试：权限边界、消息审计与同角色历史隔离。"""

import json
from types import SimpleNamespace

import pytest

import llm
import order_qa
import orchestrator
import rag
import chat_api
import models
from app import create_app
from models import (
    create_conversation,
    create_message,
    get_user_by_username,
    recent_context_messages,
)


@pytest.fixture()
def client():
    """独立测试客户端；数据库由 conftest 的 autouse fixture 重建。"""
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _login(client, username: str, password: str = "123456") -> str:
    """登录并返回 JWT。"""
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    return response.get_json()["token"]


def _headers(token: str) -> dict:
    """拼装 Bearer 认证头。"""
    return {"Authorization": f"Bearer {token}"}


def _conversation(client, token: str) -> int:
    """新建会话并返回 id。"""
    response = client.post("/api/conversations", headers=_headers(token))
    assert response.status_code == 201
    return response.get_json()["id"]


def test_non_admin_cannot_specify_query_role(client):
    """非管理员即使指定自己的实际角色也不能使用查询角色参数。"""
    token = _login(client, "employee")
    conversation_id = _conversation(client, token)
    response = client.post(
        "/api/chat",
        headers=_headers(token),
        json={
            "conversation_id": conversation_id,
            "question": "公司的价值观是什么？",
            "query_role": "employee",
        },
    )
    assert response.status_code == 403
    assert response.get_json()["code"] == "QUERY_ROLE_FORBIDDEN"


def test_admin_query_role_must_exist(client):
    """管理员指定的查询角色必须在角色白名单中。"""
    token = _login(client, "admin")
    conversation_id = _conversation(client, token)
    response = client.post(
        "/api/chat",
        headers=_headers(token),
        json={
            "conversation_id": conversation_id,
            "question": "问题",
            "query_role": "boss",
        },
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "BAD_REQUEST"


def test_standard_chat_uses_and_persists_query_role(monkeypatch, client):
    """标准模式把查询角色传给检索层，并将同一角色写入问答两条消息。"""
    captured: dict = {}

    def fake_answer(question, history=None, user_role="employee", stream=False):
        """替身：只记录收到的权限角色，不执行真实检索。"""
        captured["user_role"] = user_role
        return {"answer": "员工视角回答", "sources": []}

    monkeypatch.setattr(chat_api, "answer_question", fake_answer)
    token = _login(client, "admin")
    conversation_id = _conversation(client, token)
    response = client.post(
        "/api/chat",
        headers=_headers(token),
        json={
            "conversation_id": conversation_id,
            "question": "问题",
            "query_role": "employee",
        },
    )
    assert response.status_code == 200
    assert response.get_json()["query_role"] == "employee"
    assert captured["user_role"] == "employee"

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages", headers=_headers(token)
    ).get_json()
    assert [item["query_role"] for item in messages] == ["employee", "employee"]


def test_enhanced_chat_uses_query_role(monkeypatch, client):
    """增强模式同样接收查询角色，并通过首个 SSE 事件和消息记录暴露。"""
    captured: dict = {}
    monkeypatch.setattr(
        llm,
        "get_active_provider",
        lambda: SimpleNamespace(agent_capable=True),
    )

    def fake_enhanced(question, user_role, history, trace):
        """替身：记录增强编排收到的查询角色。"""
        captured["user_role"] = user_role
        return iter(
            [
                json.dumps({"token": "回答"}, ensure_ascii=False),
                json.dumps({"done": True, "sources": []}, ensure_ascii=False),
            ]
        )

    monkeypatch.setattr(orchestrator, "run_enhanced", fake_enhanced)
    token = _login(client, "admin")
    conversation_id = _conversation(client, token)
    response = client.post(
        "/api/chat",
        headers=_headers(token),
        json={
            "conversation_id": conversation_id,
            "question": "问题",
            "query_role": "finance",
            "mode": "enhanced",
            "stream": True,
        },
    )
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '"query_role": "finance"' in body
    assert captured["user_role"] == "finance"

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages", headers=_headers(token)
    ).get_json()
    assert [item["query_role"] for item in messages] == ["finance", "finance"]


def test_admin_order_query_obeys_simulated_role(monkeypatch, client):
    """管理员模拟 employee 时，订单问题仍按 employee 权限拒绝且不调用 LLM。"""
    monkeypatch.setattr(
        order_qa,
        "route_question",
        lambda question, history=None: {
            "intent": "order",
            "filters": {"order_no": "DD20260315004"},
            "aggregation": None,
            "fallback": False,
        },
    )

    def forbidden(*args, **kwargs):
        """替身：越权订单问题不得进入生成调用。"""
        raise AssertionError("模拟 employee 时不应调用 LLM")

    monkeypatch.setattr(rag.llm, "invoke", forbidden)
    token = _login(client, "admin")
    conversation_id = _conversation(client, token)
    response = client.post(
        "/api/chat",
        headers=_headers(token),
        json={
            "conversation_id": conversation_id,
            "question": "订单 DD20260315004 的状态？",
            "query_role": "employee",
        },
    )
    assert response.status_code == 200
    assert response.get_json()["answer"] == order_qa.ORDER_FORBIDDEN_ANSWER


def test_history_only_contains_same_query_role():
    """模型历史按查询角色隔离；旧 NULL 消息只在查询角色等于实际角色时保留。"""
    admin = get_user_by_username("admin")
    conversation_id = create_conversation(admin["id"])
    create_message(conversation_id, "user", "财务问题", query_role="admin")
    create_message(
        conversation_id, "assistant", "财务回答", query_role="admin"
    )
    create_message(conversation_id, "user", "员工问题", query_role="employee")
    create_message(
        conversation_id, "assistant", "员工回答", query_role="employee"
    )
    create_message(conversation_id, "user", "旧消息", query_role=None)

    simulated = recent_context_messages(
        conversation_id, 10, query_role="employee", actual_role="admin"
    )
    assert [item["content"] for item in simulated] == ["员工问题", "员工回答"]

    actual = recent_context_messages(
        conversation_id, 10, query_role="employee", actual_role="employee"
    )
    assert [item["content"] for item in actual] == [
        "员工问题",
        "员工回答",
        "旧消息",
    ]


def test_migration_adds_query_role_to_existing_messages_table():
    """旧库升级时自动补 query_role 列，不需要重建会话表。"""
    conn = models.get_connection()
    conn.execute("ALTER TABLE messages DROP COLUMN query_role")
    conn.commit()
    models._migrate()
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(messages)").fetchall()
    }
    assert "query_role" in columns
