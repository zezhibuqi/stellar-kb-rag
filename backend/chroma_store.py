"""Chroma 向量存储：单一 Collection，metadata 过滤，按 doc_id 清理。"""

from chromadb import PersistentClient
from langchain_chroma import Chroma as LangChainChroma
from langchain_core.documents import Document

from config import Config

COLLECTION_NAME = "enterprise_knowledge"

_client = None
_collection = None


def reset() -> None:
    """释放缓存（测试隔离用）。"""
    global _client, _collection
    _client = None
    _collection = None


def get_collection():
    global _client, _collection
    if _client is None:
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
