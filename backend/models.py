"""SQLite 数据访问层：Schema、种子数据与 CRUD 封装。

并发约定：
- 每个线程使用独立连接（sqlite3 连接默认不能跨线程共享），通过线程本地变量实现；
- WAL 模式 + timeout=30 支持多线程并发读写。
"""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from werkzeug.security import generate_password_hash

from config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    role TEXT NOT NULL DEFAULT 'employee',
    is_active INTEGER NOT NULL DEFAULT 1,
    token_version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS domains (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role TEXT NOT NULL,
    domain_name TEXT NOT NULL,
    PRIMARY KEY (role, domain_name)
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    source_content TEXT,
    domain_name TEXT NOT NULL,
    chunk_count INTEGER DEFAULT 0,
    uploaded_by INTEGER REFERENCES users(id),
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'pending',
    error_message TEXT
);
"""

ROLE_VALUES = ("employee", "finance", "sales", "aftersale", "admin")
DOCUMENT_STATUSES = ("pending", "processing", "completed", "failed")
DEFAULT_PASSWORD = "123456"

DOMAINS = [
    (1, "finance", "财务数据"),
    (2, "regulation", "规章制度"),
    (3, "product", "产品规格"),
    (4, "aftersale", "售后政策"),
    (5, "common", "公共知识"),
]

PERMISSIONS = [
    ("employee", "common"),
    ("employee", "regulation"),
    ("finance", "common"),
    ("finance", "finance"),
    ("finance", "regulation"),
    ("sales", "common"),
    ("sales", "product"),
    ("sales", "regulation"),
    ("aftersale", "common"),
    ("aftersale", "aftersale"),
    ("aftersale", "regulation"),
    ("admin", "common"),
    ("admin", "finance"),
    ("admin", "regulation"),
    ("admin", "product"),
    ("admin", "aftersale"),
]

DEFAULT_USERS = [
    ("admin", "系统管理员", "admin"),
    ("employee", "普通员工", "employee"),
    ("finance", "财务人员", "finance"),
    ("sales", "销售人员", "sales"),
    ("aftersale", "售后人员", "aftersale"),
]

_local = threading.local()


def get_connection() -> sqlite3.Connection:
    """返回当前线程的独立连接；首次调用时创建。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        Path(Config.DATABASE_URL).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            Config.DATABASE_URL,
            timeout=Config.DATABASE_TIMEOUT,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def close_connection() -> None:
    """关闭当前线程的连接（测试与清理用）。"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


@contextmanager
def transaction():
    """事务上下文：成功提交，异常回滚。"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def init_db() -> None:
    """建表并写入种子数据（幂等，可重复执行）。"""
    Path(Config.DATABASE_URL).parent.mkdir(parents=True, exist_ok=True)
    with transaction() as cur:
        cur.executescript(SCHEMA)
    _migrate()
    seed_db()


def _migrate() -> None:
    """幂等迁移：为已有数据库补充新增列。"""
    conn = get_connection()
    document_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(documents)").fetchall()
    }
    if "source_content" not in document_columns:
        conn.execute("ALTER TABLE documents ADD COLUMN source_content TEXT")
    user_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(users)").fetchall()
    }
    if "is_active" not in user_columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
        )
    if "token_version" not in user_columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0"
        )
    conn.commit()


def seed_db() -> None:
    """写入领域、权限与默认测试账号。"""
    with transaction() as cur:
        cur.executemany(
            "INSERT OR IGNORE INTO domains (id, name, display_name) VALUES (?, ?, ?)",
            DOMAINS,
        )
        cur.executemany(
            "INSERT OR IGNORE INTO role_permissions (role, domain_name) VALUES (?, ?)",
            PERMISSIONS,
        )
        for username, display_name, role in DEFAULT_USERS:
            exists = cur.execute(
                "SELECT 1 FROM users WHERE username = ?", (username,)
            ).fetchone()
            if exists is None:
                cur.execute(
                    "INSERT INTO users (username, password_hash, display_name, role) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        username,
                        generate_password_hash(DEFAULT_PASSWORD),
                        display_name,
                        role,
                    ),
                )


def create_user(
    username: str,
    password: str,
    display_name: str | None = None,
    role: str = "employee",
) -> int:
    """创建用户；重复用户名或非法参数抛 ValueError。"""
    if len(password) < 6:
        raise ValueError("密码长度不能少于 6 位")
    if role not in ROLE_VALUES:
        raise ValueError(f"非法角色：{role}")
    try:
        with transaction() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash, display_name, role) "
                "VALUES (?, ?, ?, ?)",
                (username, generate_password_hash(password), display_name, role),
            )
            return int(cur.lastrowid)
    except sqlite3.IntegrityError as exc:
        raise ValueError("用户名已存在") from exc


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with transaction() as cur:
        row = cur.execute(
            "SELECT id, username, password_hash, display_name, role, is_active, "
            "token_version, created_at "
            "FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with transaction() as cur:
        row = cur.execute(
            "SELECT id, username, display_name, role, is_active, token_version, created_at "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict[str, Any]]:
    with transaction() as cur:
        rows = cur.execute(
            "SELECT id, username, display_name, role, is_active, created_at "
            "FROM users ORDER BY id"
        ).fetchall()
        return [
            {**dict(row), "is_active": bool(row["is_active"])} for row in rows
        ]


def update_user_role(user_id: int, role: str) -> bool:
    if role not in ROLE_VALUES:
        raise ValueError(f"非法角色：{role}")
    with transaction() as cur:
        cur.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        return cur.rowcount > 0


def deactivate_user(user_id: int) -> bool:
    """软删除：标记账号停用。"""
    with transaction() as cur:
        cur.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
        return cur.rowcount > 0


def activate_user(user_id: int) -> bool:
    """恢复启用账号。"""
    with transaction() as cur:
        cur.execute("UPDATE users SET is_active = 1 WHERE id = ?", (user_id,))
        return cur.rowcount > 0


def reset_user_password(user_id: int, new_password: str) -> bool:
    """重置密码并使该用户已签发的 token 全部失效。"""
    if len(new_password) < 6:
        raise ValueError("密码长度不能少于 6 位")
    with transaction() as cur:
        cur.execute(
            "UPDATE users SET password_hash = ?, token_version = token_version + 1 "
            "WHERE id = ?",
            (generate_password_hash(new_password), user_id),
        )
        return cur.rowcount > 0


def count_active_admins() -> int:
    with transaction() as cur:
        row = cur.execute(
            "SELECT COUNT(*) AS c FROM users WHERE role = 'admin' AND is_active = 1"
        ).fetchone()
        return int(row["c"])


def count_admins() -> int:
    with transaction() as cur:
        row = cur.execute("SELECT COUNT(*) AS c FROM users WHERE role = 'admin'").fetchone()
        return int(row["c"])


def get_allowed_domains(role: str) -> list[str]:
    with transaction() as cur:
        rows = cur.execute(
            "SELECT domain_name FROM role_permissions WHERE role = ? ORDER BY domain_name",
            (role,),
        ).fetchall()
        return [row["domain_name"] for row in rows]


def create_document(
    filename: str,
    domain_name: str,
    uploaded_by: int | None = None,
    source_content: str | None = None,
) -> int:
    """创建文档记录（status=pending）；领域不存在时抛 ValueError。"""
    with transaction() as cur:
        domain = cur.execute(
            "SELECT 1 FROM domains WHERE name = ?", (domain_name,)
        ).fetchone()
        if domain is None:
            raise ValueError(f"领域不存在：{domain_name}")
        cur.execute(
            "INSERT INTO documents "
            "(filename, source_content, domain_name, chunk_count, uploaded_by, status) "
            "VALUES (?, ?, ?, 0, ?, 'pending')",
            (filename, source_content, domain_name, uploaded_by),
        )
        return int(cur.lastrowid)


def update_document_status(
    doc_id: int,
    status: str,
    chunk_count: int | None = None,
    error_message: str | None = None,
) -> bool:
    if status not in DOCUMENT_STATUSES:
        raise ValueError(f"非法状态：{status}")
    with transaction() as cur:
        if chunk_count is not None:
            cur.execute(
                "UPDATE documents SET status = ?, chunk_count = ?, error_message = ? WHERE id = ?",
                (status, chunk_count, error_message, doc_id),
            )
        else:
            cur.execute(
                "UPDATE documents SET status = ?, error_message = ? WHERE id = ?",
                (status, error_message, doc_id),
            )
        return cur.rowcount > 0


def get_document(doc_id: int) -> dict[str, Any] | None:
    with transaction() as cur:
        row = cur.execute(
            "SELECT id, filename, source_content, domain_name, chunk_count, "
            "uploaded_by, uploaded_at, status, error_message "
            "FROM documents WHERE id = ?",
            (doc_id,),
        ).fetchone()
        return dict(row) if row else None


def list_documents(domain_name: str | None = None) -> list[dict[str, Any]]:
    with transaction() as cur:
        if domain_name:
            rows = cur.execute(
                "SELECT id, filename, domain_name, chunk_count, uploaded_by, uploaded_at, "
                "status, error_message FROM documents WHERE domain_name = ? ORDER BY id",
                (domain_name,),
            ).fetchall()
        else:
            rows = cur.execute(
                "SELECT id, filename, domain_name, chunk_count, uploaded_by, uploaded_at, "
                "status, error_message FROM documents ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]


def delete_document(doc_id: int) -> bool:
    with transaction() as cur:
        cur.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        return cur.rowcount > 0
