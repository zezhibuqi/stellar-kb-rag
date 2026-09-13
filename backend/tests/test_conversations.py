"""会话与消息测试（设计文档 2.7 / 6.9）：CRUD、归属、上限、消息四态。"""

import pytest

import chroma_store
import embeddings
import order_qa
import rag
from app import create_app


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _fake_vector():
    return [0.0] * 1024


def _seed_docs(monkeypatch, docs):
    monkeypatch.setattr(
        embeddings, "embed_texts", lambda texts: [_fake_vector() for _ in texts]
    )
    for doc_id, domain, content in docs:
        chroma_store.upsert_chunks(
            doc_id,
            domain,
            f"{domain}.md",
            [
                {
                    "type": "text",
                    "content": content,
                    "start_line": 1,
                    "parent_type": "section",
                    "parent_start_line": 1,
                    "parent_end_line": 1,
                }
            ],
        )


def _identity_rerank(documents, query, top_n=5):
    return list(documents)[:top_n]


@pytest.fixture(autouse=True)
def _mock_pipeline(monkeypatch):
    monkeypatch.setattr(
        order_qa,
        "route_question",
        lambda question, history=None: {
            "intent": "knowledge",
            "filters": {},
            "aggregation": None,
            "fallback": False,
        },
    )
    monkeypatch.setattr(rag, "rerank_top_n", _identity_rerank)
    monkeypatch.setattr(rag.llm, "invoke", lambda *args, **kwargs: "回答内容")


def _login(client, username="admin", password="123456"):
    resp = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200
    return resp.get_json()["token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _new_conversation(client, token) -> int:
    resp = client.post("/api/conversations", headers=_headers(token))
    assert resp.status_code == 201
    return resp.get_json()["id"]


def test_create_list_and_delete_conversation(client):
    token = _login(client)
    conversation_id = _new_conversation(client, token)

    listed = client.get("/api/conversations", headers=_headers(token))
    assert listed.status_code == 200
    assert [row["id"] for row in listed.get_json()] == [conversation_id]

    deleted = client.delete(
        f"/api/conversations/{conversation_id}", headers=_headers(token)
    )
    assert deleted.status_code == 200
    assert client.get("/api/conversations", headers=_headers(token)).get_json() == []


def test_conversation_limit(client):
    token = _login(client)
    for _ in range(5):
        _new_conversation(client, token)

    sixth = client.post("/api/conversations", headers=_headers(token))
    assert sixth.status_code == 400
    assert sixth.get_json()["code"] == "CONVERSATION_LIMIT"


def test_other_users_conversation_is_404(client):
    admin_token = _login(client)
    conversation_id = _new_conversation(client, admin_token)
    employee_token = _login(client, "employee")

    assert (
        client.get(
            f"/api/conversations/{conversation_id}/messages",
            headers=_headers(employee_token),
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/conversations/{conversation_id}", headers=_headers(employee_token)
        ).status_code
        == 404
    )
    # 本人仍可访问
    assert (
        client.get(
            f"/api/conversations/{conversation_id}/messages",
            headers=_headers(admin_token),
        ).status_code
        == 200
    )


def test_chat_requires_conversation_id(client):
    token = _login(client)
    resp = client.post("/api/chat", headers=_headers(token), json={"question": "你好"})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "BAD_REQUEST"


def test_chat_persists_messages_and_title(monkeypatch, client):
    _seed_docs(monkeypatch, [(1, "finance", "2025年净利润为1688万元。")])
    token = _login(client)
    conversation_id = _new_conversation(client, token)
    question = "2025年净利润是多少？请给出具体数字与来源"

    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": question, "conversation_id": conversation_id},
    )
    assert resp.status_code == 200
    assert resp.get_json()["message_id"]

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages", headers=_headers(token)
    ).get_json()
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == question
    assert messages[0]["status"] == "completed"
    assert messages[1]["content"] == "回答内容"
    assert messages[1]["status"] == "completed"
    assert messages[1]["sources"][0]["filename"] == "finance.md"

    conversation = client.get("/api/conversations", headers=_headers(token)).get_json()[0]
    assert conversation["title"] == question[:20]

    client.delete(f"/api/conversations/{conversation_id}", headers=_headers(token))
    assert (
        client.get(
            f"/api/conversations/{conversation_id}/messages", headers=_headers(token)
        ).status_code
        == 404
    )


def test_stream_message_completed_with_partial_content(monkeypatch, client):
    _seed_docs(monkeypatch, [(1, "finance", "2025年净利润为1688万元。")])
    monkeypatch.setattr(rag.llm, "stream", lambda *args, **kwargs: iter(["你", "好"]))
    token = _login(client)
    conversation_id = _new_conversation(client, token)

    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "问题", "conversation_id": conversation_id, "stream": True},
    )
    body = resp.get_data(as_text=True)
    assert '"message_id"' in body
    assert 'data: {"token": "你"}' in body

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages", headers=_headers(token)
    ).get_json()
    assert messages[1]["status"] == "completed"
    assert messages[1]["content"] == "你好"


def test_stream_failure_marks_message_failed(monkeypatch, client):
    _seed_docs(monkeypatch, [(1, "finance", "2025年净利润为1688万元。")])

    def boom(*args, **kwargs):
        raise RuntimeError("上游失败")

    monkeypatch.setattr(rag.llm, "stream", boom)
    token = _login(client)
    conversation_id = _new_conversation(client, token)

    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "问题", "conversation_id": conversation_id, "stream": True},
    )
    assert "生成失败" in resp.get_data(as_text=True)

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages", headers=_headers(token)
    ).get_json()
    assert messages[1]["status"] == "failed"
