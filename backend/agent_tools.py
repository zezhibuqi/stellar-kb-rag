"""增强模式工具（设计文档 7.8）。

两个工具都以「绑定角色后返回闭包」的形式对外提供：角色在构建时固化，
规划输出无法影响权限与过滤条件。返回值统一是结构化结果——
命中 / 拒绝 / 空 都是**数据而不是异常**，由编排器决定怎么呈现。
"""

import logging

import chroma_store
import order_qa
from config import Config
from models import get_allowed_domains
from rag import DEFAULT_TOP_K, rerank_top_n

logger = logging.getLogger("agent_tools")

STATUS_OK = "ok"
STATUS_DENIED = "denied"
STATUS_EMPTY = "empty"


def build_knowledge_search(
    user_role: str,
    k: int = DEFAULT_TOP_K,
    candidates: int | None = None,
):
    """知识检索工具：权限过滤 → 重排 → 证据单元展开与去重。"""
    limit = candidates if candidates is not None else Config.AGENT_SEARCH_CANDIDATES

    def knowledge_search(query: str) -> dict:
        query = (query or "").strip()
        if not query:
            return {"status": STATUS_EMPTY, "items": []}

        allowed_domains = get_allowed_domains(user_role)
        hits = chroma_store.similarity_search(
            query, k=k, where={"domain": {"$in": allowed_domains}}
        )
        # 先多取一些再按证据单元去重，避免同一张表的多个分段占满候选位
        ranked = rerank_top_n(hits, query, top_n=max(limit * 3, limit))

        items: list[dict] = []
        seen: set = set()
        for document in ranked:
            meta = document.metadata or {}
            key = chroma_store.evidence_unit_key(
                meta.get("doc_id"),
                meta.get("parent_start_line"),
                meta.get("parent_end_line"),
            )
            if key in seen:
                continue
            seen.add(key)
            text = chroma_store.expand_evidence_unit(
                meta.get("doc_id"),
                meta.get("parent_start_line"),
                meta.get("parent_end_line"),
                meta.get("start_line"),
            )
            items.append(
                {
                    "doc_id": meta.get("doc_id"),
                    "filename": meta.get("filename", ""),
                    "domain": meta.get("domain", ""),
                    "chunk_id": meta.get("chunk_id"),
                    "chunk_type": meta.get("chunk_type", ""),
                    "start_line": meta.get("start_line"),
                    "parent_type": meta.get("parent_type", ""),
                    "parent_start_line": meta.get("parent_start_line"),
                    "parent_end_line": meta.get("parent_end_line"),
                    "text": text or document.page_content,
                }
            )
            if len(items) >= limit:
                break

        return {"status": STATUS_OK if items else STATUS_EMPTY, "items": items}

    return knowledge_search


def build_order_query(user_role: str):
    """订单查询工具：角色白名单 + 参数化 SQL（白名单校验在 order_qa 内）。"""

    def order_query(filters: dict | None = None, aggregation: str | None = None) -> dict:
        if user_role not in order_qa.ORDER_ALLOWED_ROLES:
            return {
                "status": STATUS_DENIED,
                "rows": [],
                "aggregation": None,
                "truncated": False,
            }
        try:
            result = order_qa.execute_order_query(filters or {}, aggregation)
        except Exception:  # noqa: BLE001 - 查询异常不阻塞编排
            logger.exception("订单查询执行异常，按空结果处理")
            return {
                "status": STATUS_EMPTY,
                "rows": [],
                "aggregation": None,
                "truncated": False,
            }
        hit = bool(result.get("rows")) or result.get("aggregation") is not None
        return {"status": STATUS_OK if hit else STATUS_EMPTY, **result}

    return order_query
