"""Stage 6 问答接口测试：非流式、SSE、越权、无资料、Reranker 失败。"""

import json

import pytest
from langchain_core.documents import Document

import chroma_store
import embeddings
import order_qa
import rag
from app import create_app
from reranker import RerankerError


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture(autouse=True)
def _default_knowledge_route(monkeypatch):
    """默认路由为 knowledge；订单相关用例自行覆盖。"""
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
            [{"type": "text", "content": content, "start_line": 1}],
        )


def _identity_rerank(documents, query, top_n=5):
    return list(documents[:top_n])


def _order_route(filters=None, aggregation=None, intent="order"):
    return {
        "intent": intent,
        "filters": filters or {},
        "aggregation": aggregation,
        "fallback": False,
    }


def test_non_stream_response_structure(monkeypatch, client):
    long_content = "2025年净利润为1688万元。" * 60
    _seed_docs(monkeypatch, (1, "finance", long_content))
    monkeypatch.setattr(rag, "rerank_top_n", _identity_rerank)
    monkeypatch.setattr(rag.llm, "invoke", lambda *a, **k: "2025年净利润为1688万元。")

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
    assert source["doc_id"] == 1
    assert source["chunk_id"] == 0
    assert source["chunk_type"] == "text"
    assert source["start_line"] == 1
    assert len(source["content_preview"]) == 200


def test_stream_sse_format(monkeypatch, client):
    _seed_docs(monkeypatch, (1, "finance", "2025年净利润为1688万元。"))
    monkeypatch.setattr(rag, "rerank_top_n", _identity_rerank)
    monkeypatch.setattr(rag.llm, "stream", lambda *a, **k: iter(["你", "好"]))

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
    monkeypatch.setattr(rag.llm, "invoke", lambda *a, **k: "模拟回答")

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

    def fake_invoke(prompt: str, **kwargs) -> str:
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


def test_order_route_unauthorized_returns_refusal(monkeypatch, client):
    monkeypatch.setattr(
        order_qa, "route_question", lambda q, history=None: _order_route(
            {"order_no": "DD20260315004"}
        )
    )

    def fail_invoke(prompt, **kwargs):
        raise AssertionError("越权订单不应调用 LLM")

    monkeypatch.setattr(rag.llm, "invoke", fail_invoke)
    token = _login(client, "employee")
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "订单 DD20260315004 完成了吗？"},
    )
    assert resp.status_code == 200
    assert resp.get_json() == {
        "answer": order_qa.ORDER_FORBIDDEN_ANSWER,
        "sources": [],
    }


def test_order_route_authorized_hit(monkeypatch, client):
    monkeypatch.setattr(
        order_qa, "route_question", lambda q, history=None: _order_route(
            {"order_no": "DD20260315004"}
        )
    )
    monkeypatch.setattr(rag.llm, "invoke", lambda *a, **k: "该订单已完成。")
    token = _login(client, "aftersale")
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "订单 DD20260315004 完成了吗？"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["answer"] == "该订单已完成。"
    assert len(data["sources"]) == 1
    source = data["sources"][0]
    assert source["source_type"] == "database"
    assert source["filename"] == "订单数据库（SQLite）"
    assert source["doc_id"] is None


def test_order_route_no_result_fixed(monkeypatch, client):
    monkeypatch.setattr(
        order_qa, "route_question", lambda q, history=None: _order_route(
            {"order_no": "DD99999999"}
        )
    )

    def fail_invoke(prompt, **kwargs):
        raise AssertionError("无结果订单不应调用 LLM")

    monkeypatch.setattr(rag.llm, "invoke", fail_invoke)
    token = _login(client, "aftersale")
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "订单 DD99999999 完成了吗？"},
    )
    data = resp.get_json()
    assert data["answer"] == order_qa.NO_RESULT_ANSWER
    assert data["sources"][0]["source_type"] == "database"
    assert "命中 0 条订单" in data["sources"][0]["content_preview"]


def test_mixed_unauthorized_returns_refusal_then_knowledge(monkeypatch, client):
    monkeypatch.setattr(
        order_qa, "route_question", lambda q, history=None: _order_route(
            {"order_no": "DD20260315004"}, intent="mixed"
        )
    )
    _seed_docs(monkeypatch, (1, "common", "破损商品可联系售后办理退换货。"))
    monkeypatch.setattr(rag, "rerank_top_n", _identity_rerank)
    monkeypatch.setattr(rag.llm, "invoke", lambda *a, **k: "知识回答")

    token = _login(client, "employee")
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "订单 DD20260315004 状态如何？退换货流程是什么？"},
    )
    data = resp.get_json()
    assert data["answer"] == order_qa.ORDER_FORBIDDEN_ANSWER + "\n\n" + "知识回答"
    assert data["sources"]
    assert all(s["source_type"] == "vector" for s in data["sources"])


def test_mixed_authorized_sources_both(monkeypatch, client):
    monkeypatch.setattr(
        order_qa, "route_question", lambda q, history=None: _order_route(
            {"order_no": "DD20260315004"}, intent="mixed"
        )
    )
    _seed_docs(monkeypatch, (1, "common", "破损商品可联系售后办理退换货。"))
    monkeypatch.setattr(rag, "rerank_top_n", _identity_rerank)
    monkeypatch.setattr(rag.llm, "invoke", lambda *a, **k: "综合回答")

    token = _login(client, "aftersale")
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "订单 DD20260315004 状态如何？退换货流程是什么？"},
    )
    data = resp.get_json()
    assert data["answer"] == "综合回答"
    types = {s["source_type"] for s in data["sources"]}
    assert types == {"vector", "database"}


def test_mixed_order_no_result_with_knowledge(monkeypatch, client):
    monkeypatch.setattr(
        order_qa, "route_question", lambda q, history=None: _order_route(
            {"order_no": "DD99999999"}, intent="mixed"
        )
    )
    _seed_docs(monkeypatch, (1, "common", "破损商品可联系售后办理退换货。"))
    monkeypatch.setattr(rag, "rerank_top_n", _identity_rerank)
    monkeypatch.setattr(rag.llm, "invoke", lambda *a, **k: "知识回答")

    token = _login(client, "aftersale")
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "订单 DD99999999 状态如何？退换货流程是什么？"},
    )
    data = resp.get_json()
    assert data["answer"] == order_qa.NO_RESULT_ANSWER + "\n\n" + "知识回答"
    types = {s["source_type"] for s in data["sources"]}
    assert types == {"vector", "database"}


def test_router_fallback_prefix(monkeypatch, client):
    monkeypatch.setattr(
        order_qa,
        "route_question",
        lambda q, history=None: {
            "intent": "knowledge",
            "filters": {},
            "aggregation": None,
            "fallback": True,
        },
    )
    _seed_docs(monkeypatch, (1, "common", "差旅报销需提前申请。"))
    monkeypatch.setattr(rag, "rerank_top_n", _identity_rerank)
    monkeypatch.setattr(rag.llm, "invoke", lambda *a, **k: "知识回答")

    token = _login(client, "admin")
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "订单相关的问题？"},
    )
    data = resp.get_json()
    assert data["answer"] == order_qa.ROUTER_FALLBACK_PREFIX + "\n\n" + "知识回答"


def test_order_route_sse(monkeypatch, client):
    monkeypatch.setattr(
        order_qa, "route_question", lambda q, history=None: _order_route(
            {"order_no": "DD20260315004"}
        )
    )
    monkeypatch.setattr(rag.llm, "stream", lambda *a, **k: iter(["好", "的"]))
    token = _login(client, "aftersale")
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "订单 DD20260315004 完成了吗？", "stream": True},
    )
    events = []
    for line in resp.get_data(as_text=True).splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    assert [e["token"] for e in events if "token" in e] == ["好", "的"]
    done = [e for e in events if e.get("done")]
    assert len(done) == 1
    assert done[0]["sources"][0]["source_type"] == "database"
