"""测试级配置：使用临时数据库与 Chroma 目录，并在每个用例前重建。"""

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

_TEST_DIR = tempfile.mkdtemp(prefix="stellar-kb-test-")
os.environ["DATABASE_URL"] = os.path.join(_TEST_DIR, "test_app.db")
os.environ["JWT_SECRET_KEY"] = "test-secret-key-0123456789abcdef0123456789abcdef"
os.environ["CHROMA_PERSIST_DIR"] = os.path.join(_TEST_DIR, "chroma_placeholder")

import chroma_store  # noqa: E402
from config import Config  # noqa: E402
from models import close_connection, init_db  # noqa: E402
from tasks import wait_idle  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    """每个用例使用全新数据库与 Chroma 目录，保证种子计数可预测。"""
    wait_idle(10)
    close_connection()
    for suffix in ("", "-wal", "-shm"):
        path = Config.DATABASE_URL + suffix
        if os.path.exists(path):
            os.remove(path)
    chroma_store.reset()
    Config.CHROMA_PERSIST_DIR = os.path.join(_TEST_DIR, f"chroma_{uuid.uuid4().hex}")
    init_db()
    yield
    wait_idle(10)
    close_connection()
    chroma_store.reset()


def attach_chat_conversation(test_client):
    """让 /api/chat 请求自动带上会话 id（按需创建并复用）。

    V2.0 起 /api/chat 必须携带 conversation_id 且上下文来自服务端会话记录。
    既有用例关注的是问答本身，不必每处都手工建会话，这里统一在测试客户端上
    补齐；会话与归属的显式契约由 test_conversations.py 覆盖。
    """
    original_post = test_client.post
    conversations: dict = {}

    def post(path, *args, **kwargs):
        if path == "/api/chat":
            payload = kwargs.get("json")
            if isinstance(payload, dict) and "conversation_id" not in payload:
                headers = kwargs.get("headers") or {}
                key = headers.get("Authorization", "")
                if key not in conversations:
                    created = original_post(
                        "/api/conversations", headers=headers
                    )
                    conversations[key] = created.get_json()["id"]
                kwargs["json"] = {**payload, "conversation_id": conversations[key]}
        return original_post(path, *args, **kwargs)

    test_client.post = post
    return test_client
