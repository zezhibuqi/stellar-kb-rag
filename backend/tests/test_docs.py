"""Stage 3 知识库管理接口测试：上传、轮询、删除、并发与权限。"""

import io
import time

import pytest

import chroma_store
import embeddings
import tasks
from app import create_app
from models import get_connection


@pytest.fixture()
def client():
    """独立的 Flask 测试客户端（数据库由 conftest 重建）。"""
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture(autouse=True)
def _mock_embeddings(monkeypatch):
    """默认用假向量跑通流水线；需要放慢或失败的用例自行覆盖。"""
    monkeypatch.setattr(
        embeddings,
        "embed_texts",
        lambda texts: [[0.1] * 1024 for _ in texts],
    )


def _login(client, username: str = "admin", password: str = "123456") -> str:
    """登录并返回 JWT。"""
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    return resp.get_json()["token"]


def _admin_headers(client) -> dict:
    """以 admin 身份登录后的认证头。"""
    return {"Authorization": f"Bearer {_login(client)}"}


def _upload(client, headers: dict, filename: str = "sample.md", domain: str = "finance", content: str = "line1\nline2\nline3"):
    """以 multipart 形式上传一段 Markdown，返回原始响应。"""
    return client.post(
        "/api/upload",
        headers=headers,
        data={
            "file": (io.BytesIO(content.encode("utf-8")), filename),
            "domain": domain,
        },
        content_type="multipart/form-data",
    )


def _poll_status(client, headers: dict, doc_id: int, timeout: float = 10.0):
    """轮询灌库状态直到 completed/failed；超时抛断言错误并带上观察到的状态序列。"""
    deadline = time.monotonic() + timeout
    statuses = []
    while time.monotonic() < deadline:
        resp = client.get(f"/api/docs/{doc_id}/status", headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        statuses.append(data["status"])
        if data["status"] in ("completed", "failed"):
            return data, statuses
        time.sleep(0.05)
    raise AssertionError(f"状态未在超时前终结：{statuses}")


def test_upload_status_flow_and_chunk_count(client, monkeypatch):
    """上传返回 202(pending) → 轮询能看到 processing → 完成后 chunk_count 与列表一致。"""
    def slow_embed(texts):
        """放慢向量化，保证测试能稳定观察到 processing 中间态。"""
        time.sleep(0.1)
        return [[0.1] * 1024 for _ in texts]

    monkeypatch.setattr(embeddings, "embed_texts", slow_embed)
    headers = _admin_headers(client)
    content = "line1\nline2\nline3\nline4"
    resp = _upload(client, headers, content=content)
    assert resp.status_code == 202
    assert resp.get_json()["status"] == "pending"
    doc_id = resp.get_json()["doc_id"]

    data, statuses = _poll_status(client, headers, doc_id)
    assert data["status"] == "completed"
    assert data["chunk_count"] == 1
    assert "processing" in statuses, f"应观察到 processing 中间态：{statuses}"

    rows = client.get("/api/docs?domain=finance", headers=headers).get_json()
    assert [row for row in rows if row["id"] == doc_id][0]["chunk_count"] == 1


def test_upload_validations(client):
    """上传校验：非 .md、未知领域、超过 10MB 分别返回 400/400/FILE_TOO_LARGE。"""
    headers = _admin_headers(client)
    assert _upload(client, headers, filename="notes.txt").status_code == 400
    assert _upload(client, headers, domain="unknown").status_code == 400

    big = "x" * (10 * 1024 * 1024 + 1)
    resp = _upload(client, headers, filename="big.md", content=big)
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "FILE_TOO_LARGE"


def test_failure_sets_failed_with_error(client, monkeypatch):
    """灌库抛异常时文档置 failed 且 error 字段带原始异常信息（故障可定位）。"""
    def broken_process(doc_id: int) -> None:
        """替身流水线：直接抛错，模拟 Embedding 失败。"""
        raise RuntimeError("模拟灌库异常")

    monkeypatch.setattr(tasks, "_process_document", broken_process)
    headers = _admin_headers(client)
    resp = _upload(client, headers)
    doc_id = resp.get_json()["doc_id"]
    data, _ = _poll_status(client, headers, doc_id)
    assert data["status"] == "failed"
    assert "模拟灌库异常" in data["error"]


def test_delete_during_processing_aborts(client, monkeypatch):
    """在灌库途中删除文档：任务收尾时清理已写入向量，不留下孤儿数据。"""
    def slow_embed(texts):
        """放慢向量化，为「删除发生在处理中」制造时间窗。"""
        time.sleep(0.8)
        return [[0.1] * 1024 for _ in texts]

    monkeypatch.setattr(embeddings, "embed_texts", slow_embed)
    headers = _admin_headers(client)
    resp = _upload(client, headers)
    doc_id = resp.get_json()["doc_id"]

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = client.get(f"/api/docs/{doc_id}/status", headers=headers).get_json()["status"]
        if status == "processing":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("未进入 processing 状态")

    deleted = client.delete(f"/api/docs/{doc_id}", headers=headers)
    assert deleted.status_code == 200
    time.sleep(1.2)

    rows = client.get("/api/docs", headers=headers).get_json()
    assert all(row["id"] != doc_id for row in rows)
    assert client.get(f"/api/docs/{doc_id}/status", headers=headers).status_code == 404
    assert chroma_store.count_by_doc_id(doc_id) == 0


def test_delete_missing_document_404(client):
    """删除不存在的文档返回 404。"""
    headers = _admin_headers(client)
    resp = client.delete("/api/docs/99999", headers=headers)
    assert resp.status_code == 404


def test_concurrent_uploads_max_three_workers(client, monkeypatch):
    """并发上传时后台同时处理数不超过 3（线程池上限）。"""
    active = 0
    max_active = 0
    lock = __import__("threading").Lock()

    def slow_process(doc_id: int) -> None:
        """替身流水线：睡眠 0.3s 并记录并发峰值，用于验证线程池上限为 3。"""
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.3)
        from models import update_document_status

        update_document_status(doc_id, "completed", chunk_count=1)
        with lock:
            active -= 1

    monkeypatch.setattr(tasks, "_process_document", slow_process)
    headers = _admin_headers(client)
    doc_ids = []
    for i in range(5):
        resp = _upload(client, headers, filename=f"doc{i}.md", content="hello")
        assert resp.status_code == 202
        doc_ids.append(resp.get_json()["doc_id"])

    for doc_id in doc_ids:
        _poll_status(client, headers, doc_id)
    assert max_active <= 3, f"并发超过 3：{max_active}"


def test_raw_endpoint_permissions_and_content(client):
    """原文接口：admin 可取全文，未登录 401，无权限领域 403，不存在 404，且列表不含全文。"""
    headers = _admin_headers(client)
    content = "## 测试\n原文内容。"
    resp = _upload(client, headers, content=content)
    doc_id = resp.get_json()["doc_id"]
    _poll_status(client, headers, doc_id)

    raw = client.get(f"/api/docs/{doc_id}/raw", headers=headers)
    assert raw.status_code == 200
    data = raw.get_json()
    assert data["filename"] == "sample.md"
    assert data["domain"] == "finance"
    assert data["content"] == content

    assert client.get(f"/api/docs/{doc_id}/raw").status_code == 401

    employee = _login(client, "employee")
    employee_headers = {"Authorization": f"Bearer {employee}"}
    assert (
        client.get(f"/api/docs/{doc_id}/raw", headers=employee_headers).status_code
        == 403
    )

    assert client.get("/api/docs/99999/raw", headers=headers).status_code == 404

    rows = client.get("/api/docs", headers=headers).get_json()
    assert "source_content" not in rows[0]


def test_raw_endpoint_employee_allowed_domain(client):
    """employee 访问 common 领域的原文应放行（403 只针对越权领域）。"""
    headers = _admin_headers(client)
    resp = _upload(client, headers, domain="common", content="公共内容")
    doc_id = resp.get_json()["doc_id"]
    _poll_status(client, headers, doc_id)

    employee = _login(client, "employee")
    raw = client.get(
        f"/api/docs/{doc_id}/raw",
        headers={"Authorization": f"Bearer {employee}"},
    )
    assert raw.status_code == 200
    assert raw.get_json()["content"] == "公共内容"


def test_non_admin_forbidden_on_docs_apis(client):
    """非管理员访问知识库管理接口（列表/上传/删除/状态）一律 403。"""
    token = _login(client, "employee")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/docs", headers=headers).status_code == 403
    assert _upload(client, headers).status_code == 403
    assert client.delete("/api/docs/1", headers=headers).status_code == 403
    assert client.get("/api/docs/1/status", headers=headers).status_code == 403


def test_documents_list_filter_and_empty_domains(client):
    """文档列表支持按领域过滤；无文档的领域返回空数组而不是报错。"""
    headers = _admin_headers(client)
    _upload(client, headers, domain="finance")
    _upload(client, headers, filename="handbook.md", domain="regulation", content="a\nb")
    _poll_status(client, headers, 1)
    _poll_status(client, headers, 2)

    all_rows = client.get("/api/docs", headers=headers).get_json()
    assert len(all_rows) == 2
    finance_rows = client.get("/api/docs?domain=finance", headers=headers).get_json()
    assert [row["filename"] for row in finance_rows] == ["sample.md"]
    product_rows = client.get("/api/docs?domain=product", headers=headers).get_json()
    assert product_rows == []
