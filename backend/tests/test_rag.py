"""Stage 6 问答接口测试：非流式、SSE、越权、无资料、Reranker 失败。"""

import json

import pytest
from langchain_core.documents import Document

import chroma_store
import embeddings
import order_qa
import rag
from app import create_app
from conftest import attach_chat_conversation
from reranker import RerankerError


@pytest.fixture()
def client():
    """带会话自动补全的测试客户端（本文件重点是问答本身，会话由 conftest 补齐）。"""
    app = create_app()
    app.config["TESTING"] = True
    return attach_chat_conversation(app.test_client())


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
    """登录并返回 JWT。"""
    resp = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200
    return resp.get_json()["token"]


def _headers(token: str) -> dict:
    """拼装 Bearer 认证头。"""
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
    """重排替身：保持召回顺序（用例只关心上下文与来源组装）。"""
    return list(documents[:top_n])


def _order_route(filters=None, aggregation=None, intent="order"):
    """构造路由结果，供订单/混合意图用例直接替换 order_qa.route_question。"""
    return {
        "intent": intent,
        "filters": filters or {},
        "aggregation": aggregation,
        "fallback": False,
    }


def test_non_stream_response_structure(monkeypatch, client):
    """非流式响应结构：answer + sources，来源含文件名/领域/预览与定位字段。"""
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
    """SSE 帧格式与结尾 done 事件：token 逐个下发，最后带 sources，头部禁止缓存。"""
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
    """越权检索：employee 问财务问题拿不到 finance 来源，admin 可以拿到。"""
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
    """检索为空时返回固定话术且不调用 LLM（sources 为空）。"""
    monkeypatch.setattr(rag, "search_with_permission", lambda *a, **k: [])
    token = _login(client)
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "不存在的知识"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["answer"] == "该问题超出我的知识范围"
    assert data["sources"] == []
    assert data["message_id"]


def test_reranker_failure_returns_500(monkeypatch, client):
    """重排失败不降级：返回 500 与 RERANKER_ERROR，并落一条 failed 消息。"""
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
        """替身：抛 RerankerError。"""
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
    """上下文由服务端从会话记录读取：上一轮的问答要出现在本轮 prompt 里。"""
    _seed_docs(monkeypatch, (1, "finance", "2025年净利润为1688万元。"))
    captured = {}
    calls = {"n": 0}
    monkeypatch.setattr(rag, "rerank_top_n", _identity_rerank)

    def fake_invoke(prompt: str, **kwargs) -> str:
        """记录提示词；首轮返回固定回答，便于断言历史被拼进第二轮提示词。"""
        captured["prompt"] = prompt
        calls["n"] += 1
        return "上一轮回答" if calls["n"] == 1 else "回答"

    monkeypatch.setattr(rag.llm, "invoke", fake_invoke)
    token = _login(client)
    first = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "上一轮问题"},
    )
    assert first.status_code == 200
    assert first.get_json()["answer"] == "上一轮回答"

    second = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "依据是什么？"},
    )
    assert second.status_code == 200
    prompt = captured["prompt"]
    assert "用户：上一轮问题" in prompt
    assert "助手：上一轮回答" in prompt
    assert "用户：依据是什么？" in prompt


def test_order_route_unauthorized_returns_refusal(monkeypatch, client):
    """纯订单意图越权：直接返回固定拒绝话术，绝不调用 LLM。"""
    monkeypatch.setattr(
        order_qa, "route_question", lambda q, history=None: _order_route(
            {"order_no": "DD20260315004"}
        )
    )

    def fail_invoke(prompt, **kwargs):
        """若被调用即说明越权保护失效。"""
        raise AssertionError("越权订单不应调用 LLM")

    monkeypatch.setattr(rag.llm, "invoke", fail_invoke)
    token = _login(client, "employee")
    resp = client.post(
        "/api/chat",
        headers=_headers(token),
        json={"question": "订单 DD20260315004 完成了吗？"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["answer"] == order_qa.ORDER_FORBIDDEN_ANSWER
    assert data["sources"] == []
    assert data["message_id"]


def test_order_route_authorized_hit(monkeypatch, client):
    """有权限的订单查询：答案来自模型，来源标记为数据库来源。"""
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
    """订单查不到结果：返回固定话术（不调用 LLM），来源里注明命中 0 条。"""
    monkeypatch.setattr(
        order_qa, "route_question", lambda q, history=None: _order_route(
            {"order_no": "DD99999999"}
        )
    )

    def fail_invoke(prompt, **kwargs):
        """若被调用即说明「无结果也走 LLM」的约束被破坏。"""
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
    """混合意图越权：订单部分给拒绝前缀，知识部分照常回答（来源只含向量来源）。"""
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
    """混合意图有权限：来源同时包含 vector 与 database 两类。"""
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
    """混合意图但订单无结果：拼「未查询到订单」前缀后继续回答知识部分。"""
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
    """路由回退时加显式前缀，保证「没识别出订单问题」这件事对用户可见。"""
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
    """订单问答的流式路径：token 正常下发，done 事件里带数据库来源。"""
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
