"""增强模式工具（设计文档 7.8 / ADR 0010）。

两个工具都以「绑定角色后返回闭包」的形式对外提供：角色在构建时固化，
规划输出无法影响权限与过滤条件。返回值统一是结构化结果——
命中 / 拒绝 / 空 都是**数据而不是异常**，由编排器决定怎么呈现。

知识检索为双通道：BM25 关键词通道与向量通道各自召回后用 RRF 融合，
再交给重排挑选；两条通道使用同一套「角色 → 允许领域」过滤。
"""

import logging

import chroma_store
import keyword_index
import order_qa
from config import Config
from langchain_core.documents import Document
from models import get_allowed_domains
from rag import DEFAULT_TOP_K, rerank_top_n

logger = logging.getLogger("agent_tools")

STATUS_OK = "ok"
STATUS_DENIED = "denied"
STATUS_EMPTY = "empty"


def _candidate(
    doc_id,
    chunk_id,
    filename,
    domain,
    start_line,
    parent_type,
    parent_start_line,
    parent_end_line,
    text,
) -> dict:
    return {
        "doc_id": doc_id,
        "chunk_id": chunk_id,
        "filename": filename,
        "domain": domain,
        "chunk_type": "table" if parent_type == "table" else "text",
        "start_line": start_line,
        "parent_type": parent_type,
        "parent_start_line": parent_start_line,
        "parent_end_line": parent_end_line,
        "text": text or "",
    }


def rrf_fuse(channels: list[list[dict]], top_n: int, rrf_k: int | None = None) -> list[dict]:
    """按 RRF 融合多条通道的排名（ADR 0010）：只用名次、不比较两路分值。

    同一证据单元（doc_id + 起始行）在两路都出现时得分相加，因此"被两路同时
    认可"的候选会上浮——这正是关键词通道救回"向量排第 8 却挤不进重排"的机制。
    """
    rrf_k = rrf_k if rrf_k is not None else Config.AGENT_RRF_K
    scores: dict[tuple, float] = {}
    first_seen: dict[tuple, dict] = {}
    for rows in channels:
        for rank, row in enumerate(rows, start=1):
            key = (row.get("doc_id"), row.get("start_line"))
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
            if key not in first_seen or not first_seen[key].get("text"):
                first_seen[key] = row
    ordered = sorted(scores.items(), key=lambda item: (-item[1], str(item[0])))
    return [first_seen[key] for key, _ in ordered[:top_n]]


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
            return {"status": STATUS_EMPTY, "items": [], "retrieval": {}}

        allowed_domains = get_allowed_domains(user_role)

        # 通道一：向量（语义）。与关键词通道共用同一套领域过滤。
        vector_rows = [
            _candidate(
                (document.metadata or {}).get("doc_id"),
                (document.metadata or {}).get("chunk_id"),
                (document.metadata or {}).get("filename", ""),
                (document.metadata or {}).get("domain", ""),
                (document.metadata or {}).get("start_line"),
                (document.metadata or {}).get("parent_type", ""),
                (document.metadata or {}).get("parent_start_line"),
                (document.metadata or {}).get("parent_end_line"),
                document.page_content,
            )
            for document in chroma_store.similarity_search(
                query, k=k, where={"domain": {"$in": allowed_domains}}
            )
        ]

        # 通道二：BM25 关键词（字面）。失败或索引未就绪时回退纯向量，并显式记录，
        # 避免"索引缺失"被误读成"检索没搜到"
        keyword_status = "ok"
        keyword_mode = "none"
        keyword_rows: list[dict] = []
        try:
            keyword_result = keyword_index.search(
                query, domains=allowed_domains, limit=Config.AGENT_KEYWORD_TOP_K
            )
            keyword_mode = keyword_result["mode"]
            keyword_rows = [
                _candidate(
                    row["doc_id"],
                    row["chunk_id"],
                    row["filename"],
                    row["domain"],
                    row["start_line"],
                    row["parent_type"],
                    row["parent_start_line"],
                    row["parent_end_line"],
                    row["content"],
                )
                for row in keyword_result["rows"]
            ]
        except Exception as exc:  # noqa: BLE001 - 关键词通道不得拖垮检索
            keyword_status = "error"
            logger.warning("关键词通道不可用，本次回退纯向量：%s", exc)

        fused = rrf_fuse(
            [vector_rows, keyword_rows], Config.AGENT_FUSION_TOP_N
        )
        # 融合后的候选统一成带 metadata 的 Document，交给原有的重排与证据展开
        documents = [
            Document(
                page_content=row["text"],
                metadata={
                    "doc_id": row["doc_id"],
                    "chunk_id": row["chunk_id"],
                    "filename": row["filename"],
                    "domain": row["domain"],
                    "chunk_type": row["chunk_type"],
                    "start_line": row["start_line"],
                    "parent_type": row["parent_type"],
                    "parent_start_line": row["parent_start_line"],
                    "parent_end_line": row["parent_end_line"],
                },
            )
            for row in fused
        ]
        ranked = rerank_top_n(documents, query, top_n=Config.AGENT_RERANK_TOP_N)

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

        return {
            "status": STATUS_OK if items else STATUS_EMPTY,
            "items": items,
            "retrieval": {
                "keyword_status": keyword_status,
                "keyword_mode": keyword_mode,
                "vector_hits": len(vector_rows),
                "keyword_hits": len(keyword_rows),
                "fused": len(fused),
                "reranked": len(ranked),
                "injected": len(items),
            },
        }

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
