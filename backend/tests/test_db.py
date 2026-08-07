"""Stage 1 数据层测试：Schema、种子数据、WAL、并发写入与 CRUD。"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from werkzeug.security import check_password_hash

from models import (
    close_connection,
    create_document,
    create_user,
    delete_document,
    get_allowed_domains,
    get_connection,
    get_document,
    get_user_by_username,
    list_documents,
    list_users,
    transaction,
    update_document_status,
    update_user_role,
)


def test_schema_tables_and_columns():
    conn = get_connection()
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('users', 'domains', 'role_permissions', 'documents')"
        ).fetchall()
    }
    assert tables == {"users", "domains", "role_permissions", "documents"}

    expected = {
        "users": {"id", "username", "password_hash", "display_name", "role", "created_at"},
        "domains": {"id", "name", "display_name"},
        "role_permissions": {"role", "domain_name"},
        "documents": {
            "id",
            "filename",
            "domain_name",
            "chunk_count",
            "uploaded_by",
            "uploaded_at",
            "status",
            "error_message",
        },
    }
    for table, columns in expected.items():
        actual = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        assert actual == columns, f"{table} 字段与设计文档 5.1 不一致"


def test_seed_domains_and_permissions():
    conn = get_connection()
    assert conn.execute("SELECT COUNT(*) AS c FROM domains").fetchone()["c"] == 5
    assert (
        conn.execute("SELECT COUNT(*) AS c FROM role_permissions").fetchone()["c"] == 16
    )
    admin_domains = {
        row["domain_name"]
        for row in conn.execute(
            "SELECT domain_name FROM role_permissions WHERE role = 'admin'"
        ).fetchall()
    }
    assert admin_domains == {"common", "finance", "regulation", "product", "aftersale"}


def test_seed_default_users():
    users = list_users()
    by_name = {user["username"]: user for user in users}
    assert set(by_name) == {"admin", "employee", "finance", "sales", "aftersale"}
    assert by_name["admin"]["role"] == "admin"
    assert by_name["employee"]["role"] == "employee"
    for username in by_name:
        user = get_user_by_username(username)
        assert check_password_hash(user["password_hash"], "123456")


def test_wal_enabled():
    row = get_connection().execute("PRAGMA journal_mode").fetchone()
    assert row[0].lower() == "wal"


def test_thread_independent_connections_and_concurrent_write():
    connection_ids: dict[int, int] = {}

    def write_in_thread(idx: int) -> bool:
        conn = get_connection()
        connection_ids[idx] = id(conn)
        with transaction() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash, display_name, role) "
                "VALUES (?, ?, ?, 'employee')",
                (f"thread_{idx}", "x" * 60, None),
            )
        close_connection()
        return True

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(write_in_thread, range(5)))

    assert all(results)
    assert len(set(connection_ids.values())) == 5, "每个线程应持有独立连接"
    count = (
        get_connection().execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    )
    assert count == 5 + 5


def test_user_crud_and_validations():
    with pytest.raises(ValueError):
        create_user("alice", "123")
    with pytest.raises(ValueError):
        create_user("alice", "secret123", role="boss")

    user_id = create_user("alice", "secret123", display_name="Alice", role="employee")
    created = get_user_by_username("alice")
    assert created["id"] == user_id
    assert created["display_name"] == "Alice"
    assert created["role"] == "employee"
    assert check_password_hash(created["password_hash"], "secret123")

    with pytest.raises(ValueError):
        create_user("alice", "secret123")

    assert update_user_role(user_id, "finance")
    assert get_user_by_username("alice")["role"] == "finance"
    assert not update_user_role(99999, "finance")


def test_document_crud():
    doc_id = create_document("2024年报.md", "finance", uploaded_by=1)
    assert get_document(doc_id)["status"] == "pending"
    assert get_document(doc_id)["chunk_count"] == 0

    assert update_document_status(doc_id, "processing")
    assert get_document(doc_id)["status"] == "processing"

    assert update_document_status(doc_id, "completed", chunk_count=12)
    doc = get_document(doc_id)
    assert doc["status"] == "completed"
    assert doc["chunk_count"] == 12

    assert update_document_status(doc_id, "failed", error_message="模拟异常")
    assert get_document(doc_id)["error_message"] == "模拟异常"

    assert [d["filename"] for d in list_documents("finance")] == ["2024年报.md"]
    assert list_documents("product") == []
    assert delete_document(doc_id)
    assert get_document(doc_id) is None

    with pytest.raises(ValueError):
        create_document("未知领域.md", "unknown")


def test_allowed_domains():
    assert set(get_allowed_domains("employee")) == {"common", "regulation"}
    assert set(get_allowed_domains("finance")) == {"common", "finance", "regulation"}
    assert set(get_allowed_domains("sales")) == {"common", "product", "regulation"}
    assert set(get_allowed_domains("aftersale")) == {"common", "aftersale", "regulation"}
    assert set(get_allowed_domains("admin")) == {
        "common",
        "finance",
        "regulation",
        "product",
        "aftersale",
    }


def test_persistence_after_reconnect():
    create_user("persist_user", "secret123")
    assert get_user_by_username("persist_user") is not None

    close_connection()
    count = (
        get_connection().execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    )
    assert count == 5 + 1
    assert get_user_by_username("persist_user") is not None
