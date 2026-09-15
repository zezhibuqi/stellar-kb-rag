"""关键词通道检索测试（ADR 0010）：取词、MATCH/LIKE 降级、领域过滤。"""

import keyword_index
from models import create_document


def _seed(domain="finance", filename="2025年度报告.md"):
    """写入一段含「21万吨回收量」与释义表的文档并建索引，返回 doc_id。"""
    content = "\n".join(
        [
            "## 绿色低碳",
            "公司废旧电池及材料综合回收量达到 21万吨，同比增长超六成。",
            "",
            "| 释义项 | 指 | 释义内容 |",
            "|---|---|---|",
            "| 广东邦普 | 指 | 公司合并报表子公司,广东邦普循环科技有限公司 |",
        ]
    )
    doc_id = create_document(filename, domain, source_content=content)
    keyword_index.index_document(doc_id)
    return doc_id


def test_split_terms_keeps_chinese_and_model_numbers():
    """取词规则：中文连续段整体成词，英文数字（含 - .）保持完整（如 SC-500）。"""
    assert keyword_index.split_terms("SC-500 配套电芯 2025 年回收量") == [
        "SC-500",
        "配套电芯",
        "2025",
        "年回收量",
    ]


def test_match_finds_exact_sentence():
    """三字以上中文词走 MATCH，命中包含该词的整句（21万吨 所在块）。"""
    doc_id = _seed()
    result = keyword_index.search("回收量")
    assert result["mode"] == "match"
    assert result["rows"][0]["doc_id"] == doc_id
    assert "21万吨" in result["rows"][0]["content"]


def test_short_term_falls_back_to_like():
    """两字查询词 trigram 查不了，直接走 LIKE 兜底。"""
    doc_id = _seed()
    result = keyword_index.search("释义")
    assert result["mode"] == "like"
    assert result["rows"][0]["doc_id"] == doc_id
    assert "广东邦普" in result["rows"][0]["content"]


def test_query_absent_from_corpus_returns_none():
    """语料里确实没有的词返回 none——降级不等于硬凑命中。"""
    _seed()
    assert keyword_index.search("量子")["mode"] == "none"


def test_long_chinese_phrase_falls_back_to_short_terms():
    """长片段在语料里不存在时，用它的 2 字头/尾词走 LIKE 兜底。

    「年报释义」不在文档里（文档写的是「释义项」），但「释义」在。
    """
    doc_id = _seed()
    result = keyword_index.search("年报释义 正式全称")
    assert result["mode"] == "like"
    assert result["rows"][0]["doc_id"] == doc_id


def test_domain_filter_applies_to_both_paths():
    """MATCH 与 LIKE 两条路径都要做领域过滤（漏掉即越权）。"""
    _seed(domain="finance")
    assert keyword_index.search("回收量", domains=["product"])["rows"] == []
    assert keyword_index.search("回收量", domains=["finance"])["rows"] != []
    assert keyword_index.search("释义", domains=["product"])["rows"] == []
    assert keyword_index.search("释义", domains=["finance"])["rows"] != []


def test_backfill_is_idempotent_and_marks_documents():
    """回填幂等：已入索引的文档不再重复处理；按标记判断而不是按内容。"""
    doc_id = _seed()
    assert keyword_index.missing_document_ids() == []

    conn_result = keyword_index.backfill()
    assert conn_result["indexed"] == 0, "已入索引的文档不再重复处理"

    keyword_index.delete_document(doc_id)
    # 索引被清掉但 keyword_indexed_at 仍留着：这里只验证按标记回填的语义
    assert keyword_index.search("回收量")["rows"] == []
