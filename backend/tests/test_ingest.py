"""Stage 4 灌库流水线测试：Embedding 客户端、Chroma 存储与端到端入库。"""

import io
import time

import pytest
import requests

import chroma_store
import embeddings
from app import create_app
from config import Config
from models import get_document


def _fake_vector(length: int = 1024) -> list[float]:
    return [0.1] * length


class _FakeResponse:
    def __init__(self, status_code: int, data: dict):
        self.status_code = status_code
        self._json = data
        self.text = str(data)

    def json(self):
        return self._json


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _admin_headers(client) -> dict:
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": "123456"}
    ).get_json()
    return {"Authorization": f"Bearer {login['token']}"}


def _upload(
    client, headers: dict, content: str, filename: str = "sample.md", domain: str = "finance"
):
    return client.post(
        "/api/upload",
        headers=headers,
        data={
            "file": (io.BytesIO(content.encode("utf-8")), filename),
            "domain": domain,
        },
        content_type="multipart/form-data",
    )


def _poll(client, headers: dict, doc_id: int, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    statuses = []
    while time.monotonic() < deadline:
        data = client.get(f"/api/docs/{doc_id}/status", headers=headers).get_json()
        statuses.append(data["status"])
        if data["status"] in ("completed", "failed"):
            return data, statuses
        time.sleep(0.05)
    raise AssertionError(f"状态未在超时前终结：{statuses}")


def test_embed_texts_batch_and_truncate(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        texts = json["input"]
        return _FakeResponse(
            200,
            {
                "data": [
                    {"index": i, "embedding": _fake_vector()} for i in range(len(texts))
                ]
            },
        )

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(Config, "SILICONFLOW_API_KEY", "test-key")
    texts = ["x" * (embeddings.MAX_INPUT_CHARS + 100)] + ["y"] * 40
    result = embeddings.embed_texts(texts)
    assert len(result) == 41
    assert len(calls) == 2
    assert len(calls[0]["input"][0]) <= embeddings.MAX_INPUT_CHARS
    assert calls[0]["model"] == "BAAI/bge-m3"


def test_embed_texts_retries_then_succeeds(monkeypatch):
    attempts = {"n": 0}

    def flaky_post(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise requests.ConnectionError("网络抖动")
        return _FakeResponse(200, {"data": [{"index": 0, "embedding": _fake_vector()}]})

    monkeypatch.setattr(requests, "post", flaky_post)
    monkeypatch.setattr(Config, "SILICONFLOW_API_KEY", "test-key")
    assert len(embeddings.embed_texts(["hello"])) == 1
    assert attempts["n"] == 3


def test_embed_texts_fails_after_retries(monkeypatch):
    def always_fail(*args, **kwargs):
        raise requests.ConnectionError("服务不可用")

    monkeypatch.setattr(requests, "post", always_fail)
    monkeypatch.setattr(Config, "SILICONFLOW_API_KEY", "test-key")
    with pytest.raises(RuntimeError, match="重试"):
        embeddings.embed_texts(["hello"])


def test_embed_texts_requires_api_key(monkeypatch):
    monkeypatch.setattr(Config, "SILICONFLOW_API_KEY", "")
    with pytest.raises(RuntimeError, match="SILICONFLOW_API_KEY"):
        embeddings.embed_texts(["hello"])


def test_chroma_upsert_count_delete_and_persistence(monkeypatch):
    monkeypatch.setattr(
        embeddings,
        "embed_texts",
        lambda texts: [_fake_vector() for _ in texts],
    )
    chroma_store.reset()
    chunks = [
        {"type": "text", "content": "文本内容"},
        {"type": "table", "content": "| A |"},
    ]
    assert chroma_store.upsert_chunks(1, "finance", "a.md", chunks) == 2
    assert chroma_store.count_by_doc_id(1) == 2

    chroma_store.reset()
    assert chroma_store.count_by_doc_id(1) == 2, "Chroma 应持久化"

    chroma_store.delete_by_doc_id(1)
    assert chroma_store.count_by_doc_id(1) == 0


def test_upload_pipeline_ingests_to_chroma(monkeypatch, client):
    monkeypatch.setattr(
        embeddings,
        "embed_texts",
        lambda texts: [_fake_vector() for _ in texts],
    )
    headers = _admin_headers(client)
    content = (
        "## 营收概览\n2024年营收稳步增长。\n"
        "| 产品 | 营收 |\n|---|---|\n| SC-100 | 120 |"
    )
    resp = _upload(client, headers, content)
    doc_id = resp.get_json()["doc_id"]
    data, _ = _poll(client, headers, doc_id)
    assert data["status"] == "completed"

    doc = get_document(doc_id)
    assert doc["chunk_count"] == chroma_store.count_by_doc_id(doc_id) == 2
    metadatas = chroma_store.get_collection().get(where={"doc_id": doc_id})["metadatas"]
    assert all(meta["domain"] == "finance" for meta in metadatas)
    assert all(meta["filename"] == "sample.md" for meta in metadatas)
    assert {meta["chunk_type"] for meta in metadatas} == {"text", "table"}


def test_delete_document_clears_vectors(monkeypatch, client):
    monkeypatch.setattr(
        embeddings,
        "embed_texts",
        lambda texts: [_fake_vector() for _ in texts],
    )
    headers = _admin_headers(client)
    resp = _upload(client, headers, "## 标题\n内容。")
    doc_id = resp.get_json()["doc_id"]
    _poll(client, headers, doc_id)
    assert chroma_store.count_by_doc_id(doc_id) > 0

    deleted = client.delete(f"/api/docs/{doc_id}", headers=headers)
    assert deleted.status_code == 200
    assert chroma_store.count_by_doc_id(doc_id) == 0
    assert client.get(f"/api/docs/{doc_id}/status", headers=headers).status_code == 404


def test_embedding_failure_marks_document_failed(monkeypatch, client):
    def broken_embed(texts):
        raise RuntimeError("SiliconFlow API Key 无效")

    monkeypatch.setattr(embeddings, "embed_texts", broken_embed)
    headers = _admin_headers(client)
    resp = _upload(client, headers, "内容")
    doc_id = resp.get_json()["doc_id"]
    data, _ = _poll(client, headers, doc_id)
    assert data["status"] == "failed"
    assert "API Key" in data["error"]
