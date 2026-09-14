"""关键词通道索引（ADR 0010）。

两件事：把切块文本写进 FTS5 索引（trigram 分词器，支持中文子串匹配），
以及按约定的取词规则做检索（MATCH 为主、LIKE 为降级、两条路都做领域过滤）。

注意：索引写入失败**不阻塞**灌库主流程，只在 documents.keyword_indexed_at 留痕。
"""

import logging
import re

from config import Config
from models import get_connection, get_document
from splitter import split_markdown

logger = logging.getLogger("keyword_index")

# 取词：连续中文一段、连续英文数字一段（允许 - 与 .，便于型号如 SC-500）
_TERM_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9][A-Za-z0-9._\-]*")
_MIN_MATCH_LEN = 3  # trigram 的固有限制：短于 3 字只能走 LIKE
_PHRASE_LEN = 6  # 中文片段不超过该长度时整段作短语词

_COLUMNS = (
    "doc_id",
    "chunk_id",
    "domain",
    "filename",
    "start_line",
    "parent_type",
    "parent_start_line",
    "parent_end_line",
)


def split_terms(query: str) -> list[str]:
    """按标点/空格/数字/英文把查询切成候选词。"""
    return [term for term in _TERM_RE.findall(query or "") if term]


def _fragments(term: str) -> list[str]:
    """长中文片段的补充词：头/中/尾各取 3~4 字，避免全量 trigram 造成召回噪声。"""
    size = 4 if len(term) >= 8 else 3
    middle = max(0, (len(term) - size) // 2)
    pieces = [term[:size], term[middle : middle + size], term[-size:]]
    return [piece for piece in dict.fromkeys(pieces) if len(piece) >= _MIN_MATCH_LEN]


def build_match_query(terms: list[str]) -> str:
    """拼 FTS5 MATCH 表达式：每个词包成字符串字面量、内部引号翻倍、用 OR 连接。"""
    quoted = []
    for term in terms:
        if len(term) < _MIN_MATCH_LEN:
            continue
        escaped = term.replace('"', '""')
        quoted.append(f'"{escaped}"')
    return " OR ".join(quoted)


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _domain_clause(domains: list[str] | None, params: list) -> str:
    if not domains:
        return ""
    placeholders = ", ".join("?" for _ in domains)
    params.extend(domains)
    return f" AND domain IN ({placeholders})"


def _rows(cursor) -> list[dict]:
    return [
        {
            "doc_id": row["doc_id"],
            "chunk_id": row["chunk_id"],
            "domain": row["domain"],
            "filename": row["filename"],
            "start_line": row["start_line"],
            "parent_type": row["parent_type"],
            "parent_start_line": row["parent_start_line"],
            "parent_end_line": row["parent_end_line"],
            "content": row["content"],
        }
        for row in cursor.fetchall()
    ]


def _match_terms(conn, terms: list[str], domains, per_term: int) -> list[list[dict]]:
    pools: list[list[dict]] = []
    for term in terms:
        match_query = build_match_query([term])
        if not match_query:
            continue
        params: list = [match_query]
        where = _domain_clause(domains, params)
        rows = _rows(
            conn.execute(
                f"SELECT {', '.join(_COLUMNS)}, content FROM chunk_index "
                f"WHERE chunk_index MATCH ?{where} "
                f"ORDER BY bm25(chunk_index) LIMIT ?",
                params + [per_term],
            )
        )
        if rows:
            pools.append(rows)
    return pools


def _like_terms(conn, terms: list[str], domains, per_term: int) -> list[list[dict]]:
    pools: list[list[dict]] = []
    for term in terms:
        params: list = [f"%{_escape_like(term)}%"]
        where = _domain_clause(domains, params)
        rows = _rows(
            conn.execute(
                f"SELECT {', '.join(_COLUMNS)}, content FROM chunk_index "
                f"WHERE content LIKE ? ESCAPE '\\'{where} LIMIT ?",
                params + [per_term],
            )
        )
        if rows:
            pools.append(rows)
    return pools


def _interleave(pools: list[list[dict]], limit: int) -> list[dict]:
    """按词轮转取用：让每个词都有自己的配额，避免常见词淹没稀有词。"""
    merged: list[dict] = []
    seen: set = set()
    queues = [list(pool) for pool in pools]
    while len(merged) < limit and any(queues):
        progressed = False
        for queue in queues:
            while queue and len(merged) < limit:
                row = queue.pop(0)
                key = (row["doc_id"], row["start_line"])
                if key in seen:
                    continue
                seen.add(key)
                merged.append(row)
                progressed = True
                break
        if not progressed:
            break
    return merged


def search(
    query: str, domains: list[str] | None = None, limit: int | None = None
) -> dict:
    """返回 {"rows": [...], "mode": "match"|"like"|"none"}。

    取词与执行规则（ADR 0010）：
    - 每个词**单独**查询并按词轮转取用。合成一条 OR 查询会让常见词（如年份）
      淹没有效词，把真正的目标块挤出配额。
    - 长于 3 字的中文片段额外产出 2 字头/尾词，只用于 LIKE 兜底——整段在语料里
      可能根本不存在（例：「年报释义」不存在，文档写的是「释义项」）。
    - MATCH 与 LIKE 是降级关系：短词直接 LIKE；MATCH 整条零命中时才走 LIKE。
      LIKE 使用独立配额，不占用关键词通道的召回额度。
    """
    limit = limit if limit is not None else Config.AGENT_KEYWORD_TOP_K
    terms = split_terms(query)
    if not terms:
        return {"rows": [], "mode": "none"}

    match_terms: list[str] = []
    like_terms: list[str] = []
    for term in terms:
        if len(term) >= _MIN_MATCH_LEN:
            match_terms.append(term)
            if len(term) > _MIN_MATCH_LEN and re.match(r"^[\u4e00-\u9fff]+$", term):
                like_terms.extend([term[:2], term[-2:]])
        else:
            like_terms.append(term)
    match_terms = list(dict.fromkeys(match_terms))
    like_terms = list(dict.fromkeys(like_terms))[:5]

    conn = get_connection()
    per_term = max(5, limit // max(1, len(match_terms)))
    match_pools = _match_terms(conn, match_terms, domains, per_term)
    # 降级按**词**判定而非按整条查询：短词、以及长片段派生的 2 字头/尾词，
    # 始终走 LIKE 兜底。否则「SC-300」命中就会掩盖「释义」无法字面匹配的事实。
    like_pools = _like_terms(conn, like_terms, domains, Config.AGENT_LIKE_TOP_K)
    pools = match_pools + like_pools
    if not pools:
        return {"rows": [], "mode": "none"}
    return {
        "rows": _interleave(pools, limit),
        "mode": "match" if match_pools else "like",
    }


def index_document(doc_id: int) -> int:
    """重建单个文档的关键词索引（幂等：先按 doc_id 清干净）。"""
    doc = get_document(doc_id)
    if doc is None:
        return 0
    content = doc.get("source_content") or ""
    chunks = split_markdown(content)
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM chunk_index WHERE doc_id = ?", (doc_id,))
        conn.executemany(
            "INSERT INTO chunk_index (content, doc_id, chunk_id, domain, filename, "
            "start_line, parent_type, parent_start_line, parent_end_line) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    chunk["content"],
                    doc_id,
                    index,
                    doc["domain_name"],
                    doc["filename"],
                    chunk.get("start_line", 0),
                    chunk.get("parent_type", ""),
                    chunk.get("parent_start_line", 0),
                    chunk.get("parent_end_line", 0),
                )
                for index, chunk in enumerate(chunks)
            ],
        )
        conn.execute(
            "UPDATE documents SET keyword_indexed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (doc_id,),
        )
    return len(chunks)


def delete_document(doc_id: int) -> None:
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM chunk_index WHERE doc_id = ?", (doc_id,))


def missing_document_ids() -> list[int]:
    conn = get_connection()
    return [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM documents WHERE keyword_indexed_at IS NULL ORDER BY id"
        ).fetchall()
    ]


def backfill() -> dict:
    """按缺失回填（幂等、可重复跑）。单个文档失败不影响其他文档。"""
    indexed = 0
    failed: list[int] = []
    for doc_id in missing_document_ids():
        try:
            index_document(doc_id)
            indexed += 1
        except Exception as exc:  # noqa: BLE001 - 回填失败不阻塞其余文档
            logger.warning("文档 %s 关键词索引失败：%s", doc_id, exc)
            failed.append(doc_id)
    return {"indexed": indexed, "failed": failed}
