"""检索与权限过滤 + Reranker 重排（Stage 6 在此之上组装问答）。"""

from typing import Sequence

from langchain_core.documents import Document

import chroma_store
from models import get_allowed_domains
from reranker import SiliconFlowReranker

DEFAULT_TOP_K = 10
RERANK_TOP_N = 5


def search_with_permission(
    query: str, user_role: str, k: int = DEFAULT_TOP_K
) -> list[Document]:
    """按角色权限过滤的向量检索（设计文档 7.2）。"""
    allowed_domains = get_allowed_domains(user_role)
    return chroma_store.similarity_search(
        query, k=k, where={"domain": {"$in": allowed_domains}}
    )


def rerank_top_n(
    documents: Sequence[Document],
    query: str,
    top_n: int = RERANK_TOP_N,
) -> list[Document]:
    """对检索结果重排并取 Top-N；失败时抛异常（不降级）。"""
    reranker = SiliconFlowReranker(top_n=top_n)
    return list(reranker.compress_documents(documents=documents, query=query))
