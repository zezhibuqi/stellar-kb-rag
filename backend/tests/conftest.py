"""测试级配置：使用临时数据库，并在每个用例前重建。"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

_TEST_DIR = tempfile.mkdtemp(prefix="stellar-kb-test-")
os.environ["DATABASE_URL"] = os.path.join(_TEST_DIR, "test_app.db")
os.environ["JWT_SECRET_KEY"] = "test-secret-key-0123456789abcdef0123456789abcdef"

from config import Config  # noqa: E402
from models import close_connection, init_db  # noqa: E402
from tasks import wait_idle  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    """每个用例使用全新数据库，保证种子计数可预测。"""
    wait_idle(10)
    close_connection()
    for suffix in ("", "-wal", "-shm"):
        path = Config.DATABASE_URL + suffix
        if os.path.exists(path):
            os.remove(path)
    init_db()
    yield
    close_connection()
