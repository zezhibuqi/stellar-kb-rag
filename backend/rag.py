"""检索与权限过滤 + Reranker 重排 + 问答组装（含订单结构化问答集成）。"""

import json
from typing import Sequence

from langchain_core.documents import Document

import chroma_store
import llm
import order_qa
from models import get_allowed_domains
from reranker import SiliconFlowReranker

DEFAULT_TOP_K = 10
RERANK_TOP_N = 5
SYSTEM_PROMPT = (
    "你是星辰科技集团的内部知识助手。请根据提供的参考资料回答问题。"
    "如果资料中没有相关信息，请如实说“该问题超出我的知识范围”，不要编造。"
)
ORDER_SYSTEM_PROMPT = (
    "你是星辰科技集团的内部知识助手。请根据提供的参考资料回答问题。"
    "订单数据来自订单数据库，必须严格按表格数据回答，不得估算或编造。"
    "参考资料中的订单数据即为事实依据，必须直接引用其中数值回答，"
    "任何情况下都不得回答“该问题超出我的知识范围”。"
    "仅当参考资料完全为空时才回答“该问题超出我的知识范围”。"
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
    system = (
        ORDER_SYSTEM_PROMPT
        if "【订单数据库查询结果】" in context
        else SYSTEM_PROMPT
    )
    return (
        f"{system}\n\n参考资料：\n{context}\n\n"
        f"{format_history(history)}用户：{question}"
    )


def build_sources(documents: Sequence) -> list[dict]:
    return [
        {
            "source_type": "vector",
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


def _join_prefix(*parts: str) -> str:
    return "\n\n".join(part for part in parts if part)


def _fixed_answer(text: str, sources: list, stream: bool):
    """固定话术（越权/无结果/无资料），不调用 LLM，保持流式格式一致。"""
    if stream:
        def generate():
            yield json.dumps({"token": text}, ensure_ascii=False)
            yield json.dumps({"done": True, "sources": sources}, ensure_ascii=False)

        return generate()
    return {"answer": text, "sources": sources}


def _prefixed_llm_answer(
    prefix: str, prompt: str, sources: list, stream: bool, temperature=None
):
    """前缀 + LLM 生成（前缀先作为 token 输出）。"""
    if stream:
        def generate():
            if prefix:
                yield json.dumps({"token": prefix + "\n\n"}, ensure_ascii=False)
            tokens = (
                llm.stream(prompt, temperature=temperature)
                if temperature is not None
                else llm.stream(prompt)
            )
            for token in tokens:
                yield json.dumps({"token": token}, ensure_ascii=False)
            yield json.dumps({"done": True, "sources": sources}, ensure_ascii=False)

        return generate()
    answer = (
        llm.invoke(prompt, temperature=temperature)
        if temperature is not None
        else llm.invoke(prompt)
    )
    return {
        "answer": (prefix + "\n\n" + answer) if prefix else answer,
        "sources": sources,
    }


def answer_question(
    question: str,
    history: list | None = None,
    user_role: str = "employee",
    stream: bool = False,
):
    """意图路由 → 订单查询 / 知识检索 → 合并上下文 → LLM 生成。"""
    route = order_qa.route_question(question, history)
    intent = route["intent"]

    refusal_prefix = ""
    fallback_prefix = ""
    order_context = ""
    order_hit = False
    db_sources: list = []

    if intent in ("order", "mixed"):
        prep = order_qa.prepare_order_context(route, user_role)
        if not prep["authorized"]:
            if intent == "mixed":
                refusal_prefix = order_qa.ORDER_FORBIDDEN_ANSWER
            else:
                return _fixed_answer(order_qa.ORDER_FORBIDDEN_ANSWER, [], stream)
        else:
            result = prep["result"]
            order_hit = bool(result.get("rows")) or (
                result.get("aggregation") is not None
            )
            order_context = order_qa.format_order_context(result)
            db_sources.append(order_qa.build_database_source(result))

    vector_sources: list = []
    knowledge_context = ""
    if intent in ("knowledge", "mixed"):
        candidates = search_with_permission(question, user_role, k=DEFAULT_TOP_K)
        top5 = rerank_top_n(candidates, question, top_n=RERANK_TOP_N)
        if route.get("fallback"):
            fallback_prefix = order_qa.ROUTER_FALLBACK_PREFIX
        if top5:
            vector_sources = build_sources(top5)
            knowledge_context = "\n\n---\n\n".join(
                doc.page_content for doc in top5
            )

    prefix = _join_prefix(refusal_prefix, fallback_prefix)
    answer_temperature = 0.0 if intent in ("order", "mixed") else None

    if intent == "knowledge":
        if not vector_sources:
            return _fixed_answer(_join_prefix(prefix, NO_ANSWER), [], stream)
        prompt = build_prompt(question, history, knowledge_context)
        return _prefixed_llm_answer(
            prefix, prompt, vector_sources, stream, answer_temperature
        )

    if intent == "order":
        if not order_hit:
            return _fixed_answer(order_qa.NO_RESULT_ANSWER, db_sources, stream)
        prompt = build_prompt(question, history, order_context)
        return _prefixed_llm_answer(
            prefix, prompt, db_sources, stream, answer_temperature
        )

    # intent == "mixed"
    if refusal_prefix:
        if not vector_sources:
            return _fixed_answer(prefix, [], stream)
        prompt = build_prompt(question, history, knowledge_context)
        return _prefixed_llm_answer(
            prefix, prompt, vector_sources, stream, answer_temperature
        )

    if not order_hit and not vector_sources:
        return _fixed_answer(order_qa.NO_RESULT_ANSWER, db_sources, stream)

    if not order_hit:
        prompt = build_prompt(question, history, knowledge_context)
        return _prefixed_llm_answer(
            _join_prefix(prefix, order_qa.NO_RESULT_ANSWER),
            prompt,
            vector_sources + db_sources,
            stream,
            answer_temperature,
        )

    context_parts = [order_context]
    if knowledge_context:
        context_parts.append("【知识库检索结果】\n" + knowledge_context)
    prompt = build_prompt(question, history, "\n\n".join(context_parts))
    return _prefixed_llm_answer(
        prefix, prompt, vector_sources + db_sources, stream, answer_temperature
    )
