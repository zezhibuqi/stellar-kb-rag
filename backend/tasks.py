"""后台灌库任务：模块级线程池 + 状态机（Stage 3 占位实现）。"""

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from models import close_connection, get_document, update_document_status

logger = logging.getLogger("ingest")

MAX_WORKERS = 3
PROCESS_SLEEP_SECONDS = 0.1  # 占位模拟耗时，Stage 4 替换为真实流水线

_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="ingest")
_futures: set[Future] = set()
_futures_lock = threading.Lock()


def _process_document(doc_id: int, file_path: str) -> None:
    """占位实现：pending → processing → completed，chunk_count 按行数统计。"""
    try:
        update_document_status(doc_id, "processing")
        time.sleep(PROCESS_SLEEP_SECONDS)

        if get_document(doc_id) is None:
            logger.info("文档 %s 已被删除，中止灌库", doc_id)
            return

        with open(file_path, encoding="utf-8", errors="replace") as f:
            line_count = len(f.readlines())

        if get_document(doc_id) is None:
            logger.info("文档 %s 在统计行数期间被删除，中止灌库", doc_id)
            return

        update_document_status(doc_id, "completed", chunk_count=line_count)
    except Exception as exc:  # noqa: BLE001 - 后台任务需捕获所有异常
        logger.exception("文档 %s 灌库失败", doc_id)
        if get_document(doc_id) is not None:
            update_document_status(doc_id, "failed", error_message=str(exc))


def submit_processing(doc_id: int, file_path: str) -> Future:
    """提交后台任务并登记，供测试等待空闲。"""
    future = _executor.submit(_run_task, doc_id, file_path)
    with _futures_lock:
        _futures.add(future)
    future.add_done_callback(lambda f: _forget_future(f))
    return future


def _run_task(doc_id: int, file_path: str) -> None:
    """统一入口：任务结束关闭该线程的数据库连接，避免 Windows 下文件锁。"""
    try:
        _process_document(doc_id, file_path)
    except Exception as exc:  # noqa: BLE001 - 兜底保证任务异常不会静默丢失
        logger.exception("文档 %s 灌库任务异常", doc_id)
        if get_document(doc_id) is not None:
            update_document_status(doc_id, "failed", error_message=str(exc))
    finally:
        close_connection()


def _forget_future(future: Future) -> None:
    with _futures_lock:
        _futures.discard(future)


def wait_idle(timeout: float = 10.0) -> bool:
    """等待所有已提交的后台任务结束（测试隔离用）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _futures_lock:
            pending = [f for f in _futures if not f.done()]
        if not pending:
            return True
        time.sleep(0.05)
    return False
