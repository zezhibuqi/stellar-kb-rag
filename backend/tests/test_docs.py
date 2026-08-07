"""Stage 3 知识库管理接口测试：上传、轮询、删除、并发与权限。"""

import io
import time

import pytest

import tasks
from app import create_app
from models import get_connection


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _login(client, username: str = "admin", password: str = "123456") -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    return resp.get_json()["token"]


def _admin_headers(client) -> dict:
    return {"Authorization": f"Bearer {_login(client)}"}


def _upload(client, headers: dict, filename: str = "sample.md", domain: str = "finance", content: str = "line1\nline2\nline3"):
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


def test_upload_status_flow_and_chunk_count(client):
    headers = _admin_headers(client)
    content = "line1\nline2\nline3\nline4"
    resp = _upload(client, headers, content=content)
    assert resp.status_code == 202
    assert resp.get_json()["status"] == "pending"
    doc_id = resp.get_json()["doc_id"]

    data, statuses = _poll_status(client, headers, doc_id)
    assert data["status"] == "completed"
    assert data["chunk_count"] == 4
    assert "processing" in statuses, f"应观察到 processing 中间态：{statuses}"

    rows = client.get("/api/docs?domain=finance", headers=headers).get_json()
    assert [row for row in rows if row["id"] == doc_id][0]["chunk_count"] == 4


def test_upload_validations(client):
    headers = _admin_headers(client)
    assert _upload(client, headers, filename="notes.txt").status_code == 400
    assert _upload(client, headers, domain="unknown").status_code == 400

    big = "x" * (10 * 1024 * 1024 + 1)
    resp = _upload(client, headers, filename="big.md", content=big)
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "FILE_TOO_LARGE"


def test_failure_sets_failed_with_error(client, monkeypatch):
    def broken_process(doc_id: int, file_path: str) -> None:
        raise RuntimeError("模拟灌库异常")

    monkeypatch.setattr(tasks, "_process_document", broken_process)
    headers = _admin_headers(client)
    resp = _upload(client, headers)
    doc_id = resp.get_json()["doc_id"]
    data, _ = _poll_status(client, headers, doc_id)
    assert data["status"] == "failed"
    assert "模拟灌库异常" in data["error"]


def test_delete_during_processing_aborts(client, monkeypatch):
    monkeypatch.setattr(tasks, "PROCESS_SLEEP_SECONDS", 0.8)
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


def test_delete_missing_document_404(client):
    headers = _admin_headers(client)
    resp = client.delete("/api/docs/99999", headers=headers)
    assert resp.status_code == 404


def test_concurrent_uploads_max_three_workers(client, monkeypatch):
    active = 0
    max_active = 0
    lock = __import__("threading").Lock()

    def slow_process(doc_id: int, file_path: str) -> None:
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


def test_non_admin_forbidden_on_docs_apis(client):
    token = _login(client, "employee")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/docs", headers=headers).status_code == 403
    assert _upload(client, headers).status_code == 403
    assert client.delete("/api/docs/1", headers=headers).status_code == 403
    assert client.get("/api/docs/1/status", headers=headers).status_code == 403


def test_documents_list_filter_and_empty_domains(client):
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
