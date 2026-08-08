"""后台灌库任务：模块级线程池 + 真实流水线（切片 → Embedding → Chroma）。"""

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

import chroma_store
from models import close_connection, get_document, update_document_status
from splitter import split_markdown

logger = logging.getLogger("ingest")

MAX_WORKERS = 3

_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="ingest")
_futures: set[Future] = set()
_futures_lock = threading.Lock()


def read_markdown_file(file_path: str) -> str:
    """按 UTF-8 优先、GB18030 兜底读取 Markdown。"""
    for encoding in ("utf-8", "gb18030"):
        try:
            with open(file_path, encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(file_path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _process_document(doc_id: int) -> None:
    """真实流水线：切片 → Embedding → Chroma 入库 → 更新 chunk_count。"""
    update_document_status(doc_id, "processing")
    doc = get_document(doc_id)
    if doc is None:
        logger.info("文档 %s 已被删除，中止灌库", doc_id)
        return
    content = doc.get("source_content")
    if not content:
        raise ValueError("原文档内容缺失（source_content 为空）")
    chunks = split_markdown(content)

    chroma_store.delete_by_doc_id(doc_id)  # 幂等：重复上传/重试前清理旧向量
    count = chroma_store.upsert_chunks(
        doc_id, doc["domain_name"], doc["filename"], chunks
    )

    if get_document(doc_id) is None:
        logger.info("文档 %s 在入库期间被删除，清理已写入向量", doc_id)
        chroma_store.delete_by_doc_id(doc_id)
        return
    update_document_status(doc_id, "completed", chunk_count=count)


def submit_processing(doc_id: int) -> Future:
    """提交后台任务并登记，供测试等待空闲。"""
    future = _executor.submit(_run_task, doc_id)
    with _futures_lock:
        _futures.add(future)
    future.add_done_callback(lambda f: _forget_future(f))
    return future


def _run_task(doc_id: int) -> None:
    """统一入口：任务结束关闭该线程的数据库连接，避免 Windows 下文件锁。"""
    try:
        _process_document(doc_id)
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
