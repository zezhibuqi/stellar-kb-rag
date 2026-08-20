"""Stage 8 本地全流程联调自动化测试（Mock 外部 API）。"""

import io
import time

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


@pytest.fixture(autouse=True)
def _mock_external(monkeypatch):
    monkeypatch.setattr(
        embeddings,
        "embed_texts",
        lambda texts: [[0.1] * 1024 for _ in texts],
    )
    monkeypatch.setattr(rag, "rerank_top_n", lambda docs, q, top_n=5: list(docs[:top_n]))
    monkeypatch.setattr(rag.llm, "invoke", lambda *a, **k: "模拟回答")
    monkeypatch.setattr(rag.llm, "stream", lambda *a, **k: iter(["模", "拟", "流"]))
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


def _login(client, username: str, password: str = "123456"):
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _poll_doc(client, headers: dict, doc_id: int, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/docs/{doc_id}/status", headers=headers).get_json()
        if status["status"] in ("completed", "failed"):
            return status
        time.sleep(0.05)
    raise AssertionError("灌库未在超时前完成")


def test_local_full_journey(client):
    # 健康检查
    health = client.get("/api/health").get_json()
    assert health["status"] in ("ok", "degraded")

    # admin 登录
    login = _login(client, "admin").get_json()
    admin_headers = _headers(login["token"])

    # 管理员创建用户
    created = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": "e2e_user",
            "password": "secret123",
            "display_name": "E2E",
            "role": "employee",
        },
    )
    assert created.status_code == 201

    # 新用户登录 + me
    user_login = _login(client, "e2e_user", "secret123").get_json()
    user_headers = _headers(user_login["token"])
    me = client.get("/api/auth/me", headers=user_headers).get_json()
    assert me["role"] == "employee"

    # 上传文档并轮询完成
    content = "## 企业文化\n星辰科技集团核心价值观是诚信为本、创新驱动、客户至上、绿色共赢。"
    resp = client.post(
        "/api/upload",
        headers=admin_headers,
        data={
            "file": (io.BytesIO(content.encode("utf-8")), "culture.md"),
            "domain": "common",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 202
    doc_id = resp.get_json()["doc_id"]
    status = _poll_doc(client, admin_headers, doc_id)
    assert status["status"] == "completed"
    assert chroma_store.count_by_doc_id(doc_id) == status["chunk_count"] > 0

    # 非流式问答
    resp = client.post(
        "/api/chat",
        headers=admin_headers,
        json={"question": "公司的核心价值观是什么？"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["answer"] == "模拟回答"
    assert all(s["domain"] == "common" for s in data["sources"])

    # SSE 流式问答
    resp = client.post(
        "/api/chat",
        headers=admin_headers,
        json={"question": "公司的核心价值观是什么？", "stream": True},
    )
    body = resp.get_data(as_text=True)
    assert 'data: {"token": "模"}' in body
    assert 'data: {"token": "流"}' in body
    assert '"done": true' in body

    # 越权过滤：employee 问财务问题
    chroma_store.upsert_chunks(
        999,
        "finance",
        "finance.md",
        [{"type": "text", "content": "2025年净利润为72,201,282。"}],
    )
    resp = client.post(
        "/api/chat",
        headers=user_headers,
        json={"question": "2025年净利润是多少？"},
    )
    domains = {s["domain"] for s in resp.get_json()["sources"]}
    assert domains <= {"common", "regulation"}
    assert "finance" not in domains

    # 删除文档并核对
    deleted = client.delete(f"/api/docs/{doc_id}", headers=admin_headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/docs/{doc_id}/status", headers=admin_headers).status_code == 404
    assert chroma_store.count_by_doc_id(doc_id) == 0
