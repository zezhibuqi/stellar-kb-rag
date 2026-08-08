"""检索与权限过滤 + Reranker 重排 + 问答组装（设计文档 7.3）。"""

import json
from typing import Sequence

from langchain_core.documents import Document

import chroma_store
import llm
from models import get_allowed_domains
from reranker import SiliconFlowReranker

DEFAULT_TOP_K = 10
RERANK_TOP_N = 5
SYSTEM_PROMPT = (
    "你是星辰科技集团的内部知识助手。请根据提供的参考资料回答问题。"
    "如果资料中没有相关信息，请如实说“该问题超出我的知识范围”，不要编造。"
)
NO_ANSWER = "该问题超出我的知识范围"


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


def format_history(history: list | None) -> str:
    parts = []
    for msg in history or []:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "user":
            parts.append(f"用户：{content}")
        elif role == "assistant":
            parts.append(f"助手：{content}")
    return ("\n".join(parts) + "\n") if parts else ""


def build_prompt(question: str, history: list | None, context: str) -> str:
    return (
        f"{SYSTEM_PROMPT}\n\n参考资料：\n{context}\n\n"
        f"{format_history(history)}用户：{question}"
    )


def build_sources(documents: Sequence) -> list[dict]:
    return [
        {
            "filename": doc.metadata.get("filename", ""),
            "domain": doc.metadata.get("domain", ""),
            "content_preview": doc.page_content[:200],
            "doc_id": doc.metadata.get("doc_id"),
            "chunk_id": doc.metadata.get("chunk_id"),
            "chunk_type": doc.metadata.get("chunk_type"),
            "start_line": doc.metadata.get("start_line"),
        }
        for doc in documents
    ]


def answer_question(
    question: str,
    history: list | None = None,
    user_role: str = "employee",
    stream: bool = False,
):
    """检索 → 重排 Top-5 → 拼接 Prompt → LLM 生成（支持流式/非流式）。"""
    candidates = search_with_permission(question, user_role, k=DEFAULT_TOP_K)
    top5 = rerank_top_n(candidates, question, top_n=RERANK_TOP_N)

    if not top5:
        if stream:
            def generate_no_answer():
                yield json.dumps({"token": NO_ANSWER}, ensure_ascii=False)
                yield json.dumps({"done": True, "sources": []}, ensure_ascii=False)

            return generate_no_answer()
        return {"answer": NO_ANSWER, "sources": []}

    context = "\n\n---\n\n".join(doc.page_content for doc in top5)
    prompt = build_prompt(question, history, context)
    sources = build_sources(top5)

    if stream:
        def generate():
            for token in llm.stream(prompt):
                yield json.dumps({"token": token}, ensure_ascii=False)
            yield json.dumps({"done": True, "sources": sources}, ensure_ascii=False)

        return generate()

    return {"answer": llm.invoke(prompt), "sources": sources}
