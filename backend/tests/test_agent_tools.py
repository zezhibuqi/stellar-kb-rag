"""增强模式工具测试（设计文档 7.8）：权限固化、证据单元展开与去重、拒绝/空结果为数据。"""

import pytest

import agent_tools
import chroma_store
import embeddings
import keyword_index
from models import create_document


def _fake_vector():
    """全零假向量（维度与 bge-m3 一致）。"""
    return [0.0] * 1024


def _identity_rerank(documents, query, top_n=5):
    """重排替身：保持召回顺序，只截前 top_n（让用例聚焦候选生成而非排序）。"""
    return list(documents)[:top_n]


@pytest.fixture(autouse=True)
def _mock_pipeline(monkeypatch):
    """把向量化与重排都换成替身，避免用例外呼 SiliconFlow。"""
    monkeypatch.setattr(
        embeddings, "embed_texts", lambda texts: [_fake_vector() for _ in texts]
    )
    monkeypatch.setattr(agent_tools, "rerank_top_n", _identity_rerank)


def _seed_table(domain="finance", filename="finance.md"):
    """向 Chroma 写一条带证据单元 metadata 的表格块，返回 doc_id。"""
    md = "\n".join(
        ["# 财务摘要", "", "| 项目 | 值 |", "|---|---|", "| 净利润 | 1688 |"]
    )
    doc_id = create_document(filename, domain, source_content=md)
    chroma_store.upsert_chunks(
        doc_id,
        domain,
        filename,
        [
            {
                "type": "table",
                "content": "## 财务摘要\n| 项目 | 值 |\n|---|---|\n| 净利润 | 1688 |",
                "start_line": 3,
                "parent_type": "table",
                "parent_start_line": 3,
                "parent_end_line": 5,
            }
        ],
    )
    return doc_id


def test_knowledge_search_returns_expanded_evidence_unit():
    """检索注入的是证据单元（整张表），而不是带前缀的切块文本。"""
    _seed_table()
    search = agent_tools.build_knowledge_search("admin")
    result = search("净利润")

    assert result["status"] == agent_tools.STATUS_OK
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["filename"] == "finance.md"
    assert item["parent_type"] == "table"
    # 注入的是证据单元（整张表），不是带回前缀的切块文本
    assert item["text"] == "| 项目 | 值 |\n|---|---|\n| 净利润 | 1688 |"


def test_knowledge_search_dedupes_same_evidence_unit(monkeypatch):
    """同一张表切出的多个分段只算一个证据单元，避免重复注入。"""
    md = "\n".join(["# 表", "", "| A |", "|---|", "| 1 |"])
    doc_id = create_document("t.md", "finance", source_content=md)
    chroma_store.upsert_chunks(
        doc_id,
        "finance",
        "t.md",
        [
            {
                "type": "table",
                "content": f"part-{index}",
                "start_line": 3,
                "parent_type": "table",
                "parent_start_line": 3,
                "parent_end_line": 5,
            }
            for index in range(2)
        ],
    )
    search = agent_tools.build_knowledge_search("admin")
    result = search("表")
    assert len(result["items"]) == 1, "同一张表的分段只能算一个证据单元"


def test_knowledge_search_respects_role_permissions():
    """工具内部固化角色过滤：employee 只拿到 common，admin 两个领域都能拿到。"""
    _seed_table(domain="finance", filename="finance.md")
    _seed_table(domain="common", filename="common.md")

    employee = agent_tools.build_knowledge_search("employee")("内容")
    assert {item["domain"] for item in employee["items"]} == {"common"}

    admin = agent_tools.build_knowledge_search("admin")("内容")
    assert {item["domain"] for item in admin["items"]} == {"finance", "common"}


def test_knowledge_search_empty_query_is_empty_status():
    """空查询直接返回 empty 状态，不做检索（规划器偶尔会给出空串）。"""
    assert agent_tools.build_knowledge_search("admin")("  ")["status"] == (
        agent_tools.STATUS_EMPTY
    )


def test_order_query_denied_for_employee():
    """订单工具对非白名单角色返回 denied，且不带任何行（越权是数据不是异常）。"""
    result = agent_tools.build_order_query("employee")({"order_no": "DD20260315004"})
    assert result["status"] == agent_tools.STATUS_DENIED
    assert result["rows"] == []


def test_order_query_returns_aggregation_for_aftersale():
    """aftersale 可用聚合查询，count 与种子总数一致。"""
    result = agent_tools.build_order_query("aftersale")(None, "count")
    assert result["status"] == agent_tools.STATUS_OK
    assert result["aggregation"]["value"] == 50


def test_order_query_empty_for_unknown_order():
    """查不到订单返回 empty 状态（区别于 denied：说明有权限但确实没这条）。"""
    result = agent_tools.build_order_query("aftersale")({"order_no": "DD00000000000"})
    assert result["status"] == agent_tools.STATUS_EMPTY


# ── 双通道与 RRF 融合（ADR 0010）─────────────────────────────────────────


def _row(doc_id, start_line, name="f.md", text="内容"):
    """构造一条候选行，供 RRF 与席位保护用例拼装两路召回结果。"""
    return agent_tools._candidate(
        doc_id, 0, name, "finance", start_line, "table", start_line, start_line + 2, text
    )


def test_rrf_prefers_candidate_seen_by_both_channels():
    """RRF 融合：两路都认可的候选上浮到首位，融合后保留全部去重候选。"""
    vector = [_row(1, 10), _row(2, 20)]
    keyword = [_row(2, 20), _row(3, 30)]
    fused = agent_tools.rrf_fuse([vector, keyword], top_n=3, rrf_k=60)
    assert (fused[0]["doc_id"], fused[0]["start_line"]) == (2, 20), "两路都认可的候选应上浮"
    assert len(fused) == 3, "融合后应保留全部去重候选"


def test_knowledge_search_uses_keyword_channel_when_vector_misses(monkeypatch):
    """向量通道完全召回不到时，关键词通道仍能把目标块捞进结果（ADR 0010 的核心场景）。"""
    doc_id = _seed_table()
    keyword_index.index_document(doc_id)
    # 模拟"向量通道完全召回不到、关键词通道能命中"的场景
    monkeypatch.setattr(chroma_store, "similarity_search", lambda *a, **k: [])

    search = agent_tools.build_knowledge_search("admin")
    result = search("净利润")

    assert result["status"] == agent_tools.STATUS_OK
    assert result["items"][0]["doc_id"] == doc_id
    assert result["retrieval"]["keyword_mode"] == "match"
    assert result["retrieval"]["vector_hits"] == 0


def test_knowledge_search_degrades_to_vector_when_keyword_channel_fails(monkeypatch):
    """关键词通道抛异常时回退纯向量，并在 retrieval 里标记 keyword_status=error。"""
    _seed_table()

    def boom(*args, **kwargs):
        """模拟索引未就绪：关键词查询直接抛错。"""
        raise RuntimeError("索引未就绪")

    monkeypatch.setattr(keyword_index, "search", boom)
    search = agent_tools.build_knowledge_search("admin")
    result = search("净利润")

    assert result["status"] == agent_tools.STATUS_OK, "关键词通道失败不应影响回答"
    assert result["retrieval"]["keyword_status"] == "error"
    assert result["retrieval"]["keyword_hits"] == 0


def test_keyword_channel_respects_role_permissions(monkeypatch):
    """关键词通道是绕过 Chroma 的第二条路，漏掉领域过滤就是越权。"""
    finance_id = _seed_table(domain="finance", filename="finance.md")
    keyword_index.index_document(finance_id)
    monkeypatch.setattr(chroma_store, "similarity_search", lambda *a, **k: [])

    employee = agent_tools.build_knowledge_search("employee")("净利润")
    assert employee["items"] == [], "employee 不应通过关键词通道拿到 finance 内容"
