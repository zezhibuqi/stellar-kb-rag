"""Stage 6 问答接口测试：非流式、SSE、越权、无资料、Reranker 失败。"""

import json

import pytest
from langchain_core.documents import Document

import chroma_store
import embeddings
import rag
from app import create_app
from reranker import RerankerError


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _login(client, username: str = "admin", password: str = "123456") -> str:
    resp = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200
    return resp.get_json()["token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _seed_docs(monkeypatch, *items):
    """items: (doc_id, domain, content)"""
    monkeypatch.setattr(
        embeddings,
        "embed_texts",
        lambda texts: [[0.1] * 1024 for _ in texts],
    )
    chroma_store.reset()
    for doc_id, domain, content in items:
        chroma_store.upsert_chunks(
            doc_id,
            domain,
            f"{domain}.md",
            [{"type": "text", "content": content}],
        )


def _identity_rerank(documents, query, top_n=5):
    return list(documents[:top_n])


def test_non_stream_response_structure(monkeypatch, client):
    long_content = "2025年净利润为1688万元。" * 60
    _seed_docs(monkeypatch, (1, "finance", long_content))
    monkeypatch.setattr(rag, "rerank_top_n", _identity_rerank)
    monkeypatch.setattr(rag.llm, "invoke", lambda prompt: "2025年净利润为1688万元。")

    token = _login(client)
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "2025年净利润是多少？"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["answer"] == "2025年净利润为1688万元。"
    assert len(data["sources"]) == 1
    source = data["sources"][0]
    assert source["filename"] == "finance.md"
    assert source["domain"] == "finance"
    assert len(source["content_preview"]) == 200


def test_stream_sse_format(monkeypatch, client):
    _seed_docs(monkeypatch, (1, "finance", "2025年净利润为1688万元。"))
    monkeypatch.setattr(rag, "rerank_top_n", _identity_rerank)
    monkeypatch.setattr(rag.llm, "stream", lambda prompt: iter(["你", "好"]))

    token = _login(client)
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "2025年净利润是多少？", "stream": True},
    )
    assert resp.status_code == 200
    assert resp.content_type.startswith("text/event-stream")
    assert resp.headers["Cache-Control"] == "no-cache"
    assert resp.headers["X-Accel-Buffering"] == "no"

    events = []
    for line in resp.get_data(as_text=True).splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    assert [event["token"] for event in events if "token" in event] == ["你", "好"]
    done = [event for event in events if event.get("done")]
    assert len(done) == 1
    assert done[0]["sources"][0]["domain"] == "finance"


def test_employee_cannot_get_finance_content(monkeypatch, client):
    _seed_docs(
        monkeypatch,
        (1, "finance", "2025年净利润为1688万元。"),
        (2, "regulation", "差旅报销流程：出差前需填写申请单。"),
    )
    monkeypatch.setattr(rag, "rerank_top_n", _identity_rerank)
    monkeypatch.setattr(rag.llm, "invoke", lambda prompt: "模拟回答")

    employee_token = _login(client, "employee")
    resp = client.post(
        "/api/chat",
        headers=_headers(employee_token),
        json={"question": "2025年净利润是多少？"},
    )
    assert resp.status_code == 200
    sources = resp.get_json()["sources"]
    assert all(source["domain"] in {"common", "regulation"} for source in sources)

    admin_token = _login(client, "admin")
    resp = client.post(
        "/api/chat",
        headers=_headers(admin_token),
        json={"question": "2025年净利润是多少？"},
    )
    assert resp.status_code == 200
    assert any(s["domain"] == "finance" for s in resp.get_json()["sources"])


def test_no_material_returns_fixed_answer(monkeypatch, client):
    monkeypatch.setattr(rag, "search_with_permission", lambda *a, **k: [])
    token = _login(client)
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "不存在的知识"},
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"answer": "该问题超出我的知识范围", "sources": []}


def test_reranker_failure_returns_500(monkeypatch, client):
    monkeypatch.setattr(
        rag,
        "search_with_permission",
        lambda *a, **k: [
            Document(
                page_content="内容",
                metadata={"doc_id": 1, "domain": "finance", "filename": "f.md"},
            )
        ],
    )

    def broken_rerank(*args, **kwargs):
        raise RerankerError("Reranker API 调用失败")

    monkeypatch.setattr(rag, "rerank_top_n", broken_rerank)
    token = _login(client)
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "2025年净利润是多少？"},
    )
    assert resp.status_code == 500
    assert resp.get_json()["code"] == "RERANKER_ERROR"


def test_history_included_in_prompt(monkeypatch, client):
    _seed_docs(monkeypatch, (1, "finance", "2025年净利润为1688万元。"))
    monkeypatch.setattr(rag, "rerank_top_n", _identity_rerank)
    captured = {}

    def fake_invoke(prompt: str) -> str:
        captured["prompt"] = prompt
        return "回答"

    monkeypatch.setattr(rag.llm, "invoke", fake_invoke)
    token = _login(client)
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={
            "question": "依据是什么？",
            "history": [
                {"role": "user", "content": "上一轮问题"},
                {"role": "assistant", "content": "上一轮回答"},
            ],
        },
    )
    assert resp.status_code == 200
    prompt = captured["prompt"]
    assert "用户：上一轮问题" in prompt
    assert "助手：上一轮回答" in prompt
    assert "用户：依据是什么？" in prompt
