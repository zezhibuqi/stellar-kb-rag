"""增强模式工具测试（设计文档 7.8）：权限固化、证据单元展开与去重、拒绝/空结果为数据。"""

import pytest

import agent_tools
import chroma_store
import embeddings
from models import create_document


def _fake_vector():
    return [0.0] * 1024


def _identity_rerank(documents, query, top_n=5):
    return list(documents)[:top_n]


@pytest.fixture(autouse=True)
def _mock_pipeline(monkeypatch):
    monkeypatch.setattr(
        embeddings, "embed_texts", lambda texts: [_fake_vector() for _ in texts]
    )
    monkeypatch.setattr(agent_tools, "rerank_top_n", _identity_rerank)


def _seed_table(domain="finance", filename="finance.md"):
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
    _seed_table(domain="finance", filename="finance.md")
    _seed_table(domain="common", filename="common.md")

    employee = agent_tools.build_knowledge_search("employee")("内容")
    assert {item["domain"] for item in employee["items"]} == {"common"}

    admin = agent_tools.build_knowledge_search("admin")("内容")
    assert {item["domain"] for item in admin["items"]} == {"finance", "common"}


def test_knowledge_search_empty_query_is_empty_status():
    assert agent_tools.build_knowledge_search("admin")("  ")["status"] == (
        agent_tools.STATUS_EMPTY
    )


def test_order_query_denied_for_employee():
    result = agent_tools.build_order_query("employee")({"order_no": "DD20260315004"})
    assert result["status"] == agent_tools.STATUS_DENIED
    assert result["rows"] == []


def test_order_query_returns_aggregation_for_aftersale():
    result = agent_tools.build_order_query("aftersale")(None, "count")
    assert result["status"] == agent_tools.STATUS_OK
    assert result["aggregation"]["value"] == 50


def test_order_query_empty_for_unknown_order():
    result = agent_tools.build_order_query("aftersale")({"order_no": "DD00000000000"})
    assert result["status"] == agent_tools.STATUS_EMPTY
