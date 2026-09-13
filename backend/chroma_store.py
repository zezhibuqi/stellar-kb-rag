"""Chroma 向量存储：单一 Collection，metadata 过滤，按 doc_id 清理。"""

import threading

from chromadb import PersistentClient
from langchain_chroma import Chroma as LangChainChroma
from langchain_core.documents import Document

from config import Config

COLLECTION_NAME = "enterprise_knowledge"

_client = None
_collection = None
# 客户端与集合必须成对初始化：后台灌库线程与请求线程会并发触发懒加载，
# 没有锁的话可能出现「客户端已建、集合还是 None」的半初始化状态
_lock = threading.Lock()


def reset() -> None:
    """释放缓存（测试隔离用）。"""
    global _client, _collection
    with _lock:
        _client = None
        _collection = None


def get_collection():
    global _client, _collection
    with _lock:
        if _client is None or _collection is None:
            _client = PersistentClient(path=Config.CHROMA_PERSIST_DIR)
            _collection = _client.get_or_create_collection(name=COLLECTION_NAME)
        return _collection


def upsert_chunks(
    doc_id: int, domain: str, filename: str, chunks: list[dict]
) -> int:
    """将切块向量化后写入单一 Collection，返回写入数量。"""
    import embeddings

    if not chunks:
        return 0
    texts = [chunk["content"] for chunk in chunks]
    vectors = embeddings.embed_texts(texts)
    collection = get_collection()
    collection.upsert(
        ids=[f"{doc_id}_{i}" for i in range(len(chunks))],
        documents=texts,
        metadatas=[
            {
                "doc_id": doc_id,
                "domain": domain,
                "filename": filename,
                "chunk_id": i,
                "chunk_type": chunk["type"],
                "start_line": chunk.get("start_line", 0),
                "parent_type": chunk.get("parent_type", ""),
                "parent_start_line": chunk.get("parent_start_line", 0),
                "parent_end_line": chunk.get("parent_end_line", 0),
            }
            for i, chunk in enumerate(chunks)
        ],
        embeddings=vectors,
    )
    return len(chunks)


def delete_by_doc_id(doc_id: int) -> None:
    get_collection().delete(where={"doc_id": doc_id})


def count_by_doc_id(doc_id: int) -> int:
    result = get_collection().get(where={"doc_id": doc_id})
    return len(result["ids"])


def _langchain_collection() -> LangChainChroma:
    """返回共享同一 PersistentClient 的 LangChain Chroma 封装。"""
    from embeddings import SiliconFlowEmbeddings

    get_collection()  # 确保 _client 已初始化
    return LangChainChroma(
        client=_client,
        collection_name=COLLECTION_NAME,
        embedding_function=SiliconFlowEmbeddings(),
    )


def similarity_search(
    query: str, k: int = 10, where: dict | None = None
) -> list[Document]:
    return _langchain_collection().similarity_search(query, k=k, filter=where)


def similarity_search_with_scores(
    query: str, k: int = 10, where: dict | None = None
) -> list[tuple[Document, float]]:
    return _langchain_collection().similarity_search_with_relevance_scores(
        query, k=k, filter=where
    )


def evidence_unit_key(
    doc_id: int, parent_start_line: int | None, parent_end_line: int | None
) -> tuple:
    """证据单元去重键（ADR 0009）：同一张表的多个分段只展开一次。"""
    return (int(doc_id), int(parent_start_line or 0), int(parent_end_line or 0))


def expand_evidence_unit(
    doc_id: int,
    parent_start_line: int | None,
    parent_end_line: int | None,
    hit_start_line: int | None = None,
    max_chars: int | None = None,
) -> str:
    """按证据单元行区间从原文档（documents.source_content）切片展开。

    超过 max_chars 时以命中段为中心向两侧扩展，只添加整行，不切断行。
    """
    from models import get_document

    doc = get_document(doc_id)
    if doc is None:
        return ""
    content = doc.get("source_content") or ""
    if not content:
        return ""

    lines = content.split("\n")
    total = len(lines)
    start = max(1, int(parent_start_line or 1))
    end = min(total, int(parent_end_line or total))
    if end < start:
        return ""

    limit = max_chars if max_chars is not None else Config.AGENT_EVIDENCE_MAX_CHARS
    return _truncate_around(lines[start - 1 : end], start, hit_start_line, limit)


def _truncate_around(
    lines: list[str], first_line_no: int, hit_start_line: int | None, limit: int
) -> str:
    """以命中段为中心向两侧补齐整行，直到达到字符上限。"""
    if sum(len(line) + 1 for line in lines) <= limit:
        return "\n".join(lines)

    if hit_start_line:
        hit_index = min(max(int(hit_start_line) - first_line_no, 0), len(lines) - 1)
    else:
        hit_index = 0

    low = high = hit_index
    used = len(lines[hit_index])
    while True:
        grew = False
        if high + 1 < len(lines) and used + len(lines[high + 1]) + 1 <= limit:
            high += 1
            used += len(lines[high]) + 1
            grew = True
        if low - 1 >= 0 and used + len(lines[low - 1]) + 1 <= limit:
            low -= 1
            used += len(lines[low]) + 1
            grew = True
        if not grew:
            break
    return "\n".join(lines[low : high + 1])
