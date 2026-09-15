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
    error_message TEXT,
    keyword_indexed_at TIMESTAMP   -- 关键词索引写入时间（ADR 0010），未入索引为 NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT UNIQUE NOT NULL,
    customer_name TEXT NOT NULL,
    contact TEXT,
    product_type TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    payment_method TEXT NOT NULL,
    total_amount REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 会话（每用户最多保留 CONVERSATION_LIMIT 个，见设计文档 2.7）
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 会话消息（含所用模式、状态与增强模式的过程记录）
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,                        -- user | assistant
    content TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'standard',     -- standard | enhanced
    status TEXT NOT NULL DEFAULT 'completed',  -- streaming | completed | failed | aborted
    sources_json TEXT,                         -- 引用来源数组（含 sub_question_id）
    trace_json TEXT,                           -- 增强模式过程记录
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);

-- 关键词通道索引（ADR 0010）：FTS5 + trigram 分词器，支持中文子串匹配；
-- 短于 3 字的查询词与 MATCH 零命中的情况由 LIKE 兜底，不落在这张表上。
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_index USING fts5(
    content,
    doc_id UNINDEXED,
    chunk_id UNINDEXED,
    domain UNINDEXED,
    filename UNINDEXED,
    start_line UNINDEXED,
    parent_type UNINDEXED,
    parent_start_line UNINDEXED,
    parent_end_line UNINDEXED,
    tokenize=trigram
);
"""

ROLE_VALUES = ("employee", "finance", "sales", "aftersale", "admin")
DOCUMENT_STATUSES = ("pending", "processing", "completed", "failed")
CONVERSATION_LIMIT = 5
MESSAGE_STATUSES = ("streaming", "completed", "failed", "aborted")
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
    _seed_orders()


def _seed_orders() -> None:
    """写入确定性订单种子数据（幂等）。"""
    from orders_seed import seed_orders

    seed_orders()


def _migrate() -> None:
    """幂等迁移：为已有数据库补充新增列。"""
    conn = get_connection()
    document_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(documents)").fetchall()
    }
    if "source_content" not in document_columns:
        conn.execute("ALTER TABLE documents ADD COLUMN source_content TEXT")
    if "keyword_indexed_at" not in document_columns:
        conn.execute("ALTER TABLE documents ADD COLUMN keyword_indexed_at TIMESTAMP")
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
    """按用户名取用户（含 password_hash 与 token_version，供登录与改密校验用）。"""
    with transaction() as cur:
        row = cur.execute(
            "SELECT id, username, password_hash, display_name, role, is_active, "
            "token_version, created_at "
            "FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    """按主键取用户（不含密码哈希）；认证装饰器与用户接口都走这里。"""
    with transaction() as cur:
        row = cur.execute(
            "SELECT id, username, display_name, role, is_active, token_version, created_at "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict[str, Any]]:
    """全部用户，按 id 升序；is_active 统一转成 bool 供前端直接使用。"""
    with transaction() as cur:
        rows = cur.execute(
            "SELECT id, username, display_name, role, is_active, created_at "
            "FROM users ORDER BY id"
        ).fetchall()
        return [
            {**dict(row), "is_active": bool(row["is_active"])} for row in rows
        ]


def update_user_role(user_id: int, role: str) -> bool:
    """修改角色；非法角色抛 ValueError，返回是否命中记录。"""
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


def delete_user(user_id: int) -> bool:
    """永久删除用户；其上传的文档保留，上传者置空（应用层 SET NULL，
    现有表无法追加 ON DELETE SET NULL 外键动作）。"""
    with transaction() as cur:
        cur.execute(
            "UPDATE documents SET uploaded_by = NULL WHERE uploaded_by = ?",
            (user_id,),
        )
        cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
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
    """当前可用（未停用）的 admin 数量，用于「不能停用最后一个管理员」的护栏。"""
    with transaction() as cur:
        row = cur.execute(
            "SELECT COUNT(*) AS c FROM users WHERE role = 'admin' AND is_active = 1"
        ).fetchone()
        return int(row["c"])


def count_admins() -> int:
    """admin 总数（含已停用），用于「不能降级最后一个管理员」的护栏。"""
    with transaction() as cur:
        row = cur.execute("SELECT COUNT(*) AS c FROM users WHERE role = 'admin'").fetchone()
        return int(row["c"])


def get_allowed_domains(role: str) -> list[str]:
    """角色可访问的知识领域；检索与原文接口都靠它做权限过滤。"""
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
    """更新灌库状态；传 chunk_count 时一并写入（成功路径），否则只写状态与错误信息。

    失败态必须带 error_message，否则前端只能显示「灌库失败」而无法定位原因。
    """
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
    """取单篇文档（含 source_content 全文）；证据单元展开与原文接口的取数入口。"""
    with transaction() as cur:
        row = cur.execute(
            "SELECT id, filename, source_content, domain_name, chunk_count, "
            "uploaded_by, uploaded_at, status, error_message "
            "FROM documents WHERE id = ?",
            (doc_id,),
        ).fetchone()
        return dict(row) if row else None


def list_documents(domain_name: str | None = None) -> list[dict[str, Any]]:
    """文档列表；**不返回 source_content**（列表页不需要全文，避免响应体过大）。"""
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
    """删除文档记录；对应向量与关键词索引由 docs_api 在同一请求内先行清理。"""
    with transaction() as cur:
        cur.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        return cur.rowcount > 0


def get_setting(key: str, default: str | None = None) -> str | None:
    """读取应用级设置；表不存在（旧库未迁移）时按未设置处理。"""
    try:
        with transaction() as cur:
            row = cur.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default
    except sqlite3.OperationalError:
        return default


def set_setting(key: str, value: str) -> None:
    """写入应用级设置（upsert）。"""
    with transaction() as cur:
        cur.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# ── 会话与消息（设计文档 2.7 / 6.9）────────────────────────────────────────


def count_conversations(user_id: int) -> int:
    """本人会话数，用于创建前的上限校验。"""
    with transaction() as cur:
        row = cur.execute(
            "SELECT COUNT(*) AS c FROM conversations WHERE user_id = ?", (user_id,)
        ).fetchone()
        return int(row["c"])


def create_conversation(user_id: int, title: str = "") -> int:
    """新建会话；超过每用户上限时抛 ValueError。"""
    with transaction() as cur:
        row = cur.execute(
            "SELECT COUNT(*) AS c FROM conversations WHERE user_id = ?", (user_id,)
        ).fetchone()
        if int(row["c"]) >= CONVERSATION_LIMIT:
            raise ValueError(f"最多保留 {CONVERSATION_LIMIT} 个会话，请先删除一个")
        cur.execute(
            "INSERT INTO conversations (user_id, title) VALUES (?, ?)",
            (user_id, title),
        )
        return int(cur.lastrowid)


def list_conversations(user_id: int) -> list[dict[str, Any]]:
    """本人会话列表，按最近更新倒序（最近聊的排最前）。"""
    with transaction() as cur:
        rows = cur.execute(
            "SELECT id, title, created_at, updated_at FROM conversations "
            "WHERE user_id = ? ORDER BY updated_at DESC, id DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_conversation(conversation_id: int) -> dict[str, Any] | None:
    """按 id 取会话（含 user_id）；归属校验由 conversations_api 负责。"""
    with transaction() as cur:
        row = cur.execute(
            "SELECT id, user_id, title, created_at, updated_at FROM conversations "
            "WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        return dict(row) if row else None


def update_conversation_title(conversation_id: int, title: str) -> bool:
    """更新会话标题（首条提问的前 20 字），同时刷新 updated_at。"""
    with transaction() as cur:
        cur.execute(
            "UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (title, conversation_id),
        )
        return cur.rowcount > 0


def touch_conversation(conversation_id: int) -> None:
    """刷新会话更新时间（新消息落库后调用）。"""
    with transaction() as cur:
        cur.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conversation_id,),
        )


def delete_conversation(conversation_id: int) -> bool:
    """删除会话；消息由外键级联删除（连接已开启 PRAGMA foreign_keys=ON）。"""
    with transaction() as cur:
        cur.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return cur.rowcount > 0


def create_message(
    conversation_id: int,
    role: str,
    content: str = "",
    mode: str = "standard",
    status: str = "completed",
    sources_json: str | None = None,
    trace_json: str | None = None,
) -> int:
    """写入一条消息并返回 id；流式回答先以 status=streaming 落库再逐步补全。

    sources_json / trace_json 由调用方序列化后传入，读取时由接口层还原成对象。
    """
    if status not in MESSAGE_STATUSES:
        raise ValueError(f"非法消息状态：{status}")
    with transaction() as cur:
        cur.execute(
            "INSERT INTO messages (conversation_id, role, content, mode, status, "
            "sources_json, trace_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (conversation_id, role, content, mode, status, sources_json, trace_json),
        )
        return int(cur.lastrowid)


def update_message(
    message_id: int,
    content: str | None = None,
    status: str | None = None,
    sources_json: str | None = None,
    trace_json: str | None = None,
) -> bool:
    """按需更新消息字段（只更新显式传入的部分）；四个字段都为空时直接返回 False。"""
    fields: list[str] = []
    params: list[Any] = []
    if content is not None:
        fields.append("content = ?")
        params.append(content)
    if status is not None:
        if status not in MESSAGE_STATUSES:
            raise ValueError(f"非法消息状态：{status}")
        fields.append("status = ?")
        params.append(status)
    if sources_json is not None:
        fields.append("sources_json = ?")
        params.append(sources_json)
    if trace_json is not None:
        fields.append("trace_json = ?")
        params.append(trace_json)
    if not fields:
        return False
    params.append(message_id)
    with transaction() as cur:
        cur.execute(f"UPDATE messages SET {', '.join(fields)} WHERE id = ?", params)
        return cur.rowcount > 0


def get_message(message_id: int) -> dict[str, Any] | None:
    """取单条消息（含 mode/status/sources_json/trace_json），用于状态回查。"""
    with transaction() as cur:
        row = cur.execute(
            "SELECT id, conversation_id, role, content, mode, status, "
            "sources_json, trace_json, created_at FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        return dict(row) if row else None


def list_messages(conversation_id: int) -> list[dict[str, Any]]:
    """按写入顺序取会话全部消息，供前端回放（含失败/中断的部分内容）。"""
    with transaction() as cur:
        rows = cur.execute(
            "SELECT id, role, content, mode, status, sources_json, trace_json, "
            "created_at FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def recent_context_messages(conversation_id: int, turns: int) -> list[dict[str, Any]]:
    """最近 N 轮的问答文本（按时间正序），供服务端拼上下文。

    只取已完成且内容非空的消息；失败与中断的消息不计入。
    """
    limit = max(turns, 0) * 2
    if limit == 0:
        return []
    with transaction() as cur:
        rows = cur.execute(
            "SELECT id, role, content FROM messages "
            "WHERE conversation_id = ? AND status = 'completed' AND content <> '' "
            "ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]
