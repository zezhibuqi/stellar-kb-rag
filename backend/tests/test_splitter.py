"""切片器测试：切分、上下文前缀与证据单元（设计文档 7.1 / ADR 0009）。"""

import logging

from chroma_store import evidence_unit_key, expand_evidence_unit
from models import create_document
from splitter import split_markdown


def test_pure_text_splits_into_text_chunks():
    content = "\n".join(f"第{i}行内容" * 20 for i in range(200))
    chunks = split_markdown(content, chunk_size=200, chunk_overlap=20)
    assert len(chunks) > 1
    assert all(chunk["type"] == "text" for chunk in chunks)


def test_pure_table_kept_whole():
    table = "| 型号 | 容量 |\n|---|---|\n| SC-100 | 100Ah |"
    chunks = split_markdown(table)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk["type"] == "table"
    assert chunk["content"] == table  # 无标题也无说明行 → 不加前缀
    assert chunk["start_line"] == 1
    assert chunk["parent_type"] == "table"
    assert (chunk["parent_start_line"], chunk["parent_end_line"]) == (1, 3)


def test_mixed_text_and_table():
    md = "## 概述\n这是文本。\n| A | B |\n|---|---|\n| 1 | 2 |\n结尾文字。"
    chunks = split_markdown(md)
    types = [chunk["type"] for chunk in chunks]
    assert "text" in types
    assert "table" in types
    table_chunk = next(chunk for chunk in chunks if chunk["type"] == "table")
    # 前缀 = 最近标题 + 表格上方最近的非空非表格行
    assert table_chunk["content"] == "## 概述\n这是文本。\n| A | B |\n|---|---|\n| 1 | 2 |"
    assert table_chunk["start_line"] == 3
    assert table_chunk["parent_type"] == "table"


def test_oversized_table_split_with_header():
    rows = [f"| 产品{i} | 数据{i} |" for i in range(30)]
    table = "| 产品 | 数据 |\n|---|---|\n" + "\n".join(rows)
    chunks = split_markdown(table, chunk_size=120)
    assert len(chunks) > 1
    assert all(chunk["type"] == "table" for chunk in chunks)
    for chunk in chunks:
        assert chunk["content"].startswith("| 产品 | 数据 |\n|---|---|\n")
        assert chunk["content"].count("|---|---|") == 1, "分隔行不得重复"
        assert chunk["start_line"] == 1


def test_oversized_header_kept_whole_with_warning(caplog):
    header = "| " + "x" * 300 + " |"
    table = header + "\n| " + "y" * 50 + " |"
    with caplog.at_level(logging.WARNING, logger="splitter"):
        chunks = split_markdown(table, chunk_size=100)
    assert len(chunks) == 1
    assert chunks[0]["content"] == table
    assert chunks[0]["start_line"] == 1
    assert any("表头超过" in record.message for record in caplog.records)


def test_table_without_separator_line():
    table = "| 产品 | 数据 |\n| A | 1 |\n| B | 2 |"
    chunks = split_markdown(table, chunk_size=20)
    assert len(chunks) == 2
    assert all(chunk["type"] == "table" for chunk in chunks)


def test_text_chunk_start_line_mapping():
    content = "标题行\n\n" + "\n".join(
        f"第{i}行：" + "这是一段很长很长的文本内容。" * 20 for i in range(50)
    )
    chunks = split_markdown(content, chunk_size=60, chunk_overlap=10)
    assert len(chunks) > 1
    assert chunks[0]["type"] == "text"
    assert chunks[0]["start_line"] == 1
    start_lines = [chunk["start_line"] for chunk in chunks]
    assert all(start_lines[i] <= start_lines[i + 1] for i in range(len(start_lines) - 1))
    assert start_lines[-1] > start_lines[0]


def test_table_start_line_after_text():
    md = "第一行\n第二行\n| A | B |\n|---|---|\n| 1 | 2 |"
    chunks = split_markdown(md)
    table_chunk = next(chunk for chunk in chunks if chunk["type"] == "table")
    assert table_chunk["start_line"] == 3


def _only_table(md: str, **kwargs) -> dict:
    chunks = split_markdown(md, **kwargs)
    tables = [chunk for chunk in chunks if chunk["type"] == "table"]
    assert len(tables) == 1
    return tables[0]


def test_caption_taken_across_blank_lines():
    """表格上方的说明行（表名/单位）与表格之间隔空行时仍要取到。"""
    md = "## 营收概况\n\n单位：万元\n\n| 项目 | 营收 |\n|---|---|\n| 动力电池 | 100 |"
    chunk = _only_table(md)
    assert chunk["content"].startswith("## 营收概况\n单位：万元\n| 项目 | 营收 |")


def test_caption_not_duplicated_when_line_above_is_heading():
    """表格上方直接是标题时不重复拼接，前缀只保留最近标题。"""
    md = "## 营收概况\n### 分产品\n| 项目 | 营收 |\n|---|---|\n| 动力电池 | 100 |"
    chunk = _only_table(md)
    assert chunk["content"] == "### 分产品\n| 项目 | 营收 |\n|---|---|\n| 动力电池 | 100 |"


def test_table_at_document_start_has_no_prefix():
    md = "| 项目 | 营收 |\n|---|---|\n| 动力电池 | 100 |"
    chunk = _only_table(md)
    assert chunk["content"] == md
    assert (chunk["parent_start_line"], chunk["parent_end_line"]) == (1, 3)


def test_table_with_heading_only():
    """只有标题、没有说明行的表格：前缀就是标题本身。"""
    md = "## 概况\n| A | B |\n|---|---|\n| 1 | 2 |"
    chunk = _only_table(md)
    assert chunk["content"] == "## 概况\n| A | B |\n|---|---|\n| 1 | 2 |"


def test_oversized_table_every_segment_carries_prefix():
    rows = [f"| 产品{i} | 数据{i} |" for i in range(30)]
    md = "## 产品参数表\n\n| 产品 | 数据 |\n|---|---|\n" + "\n".join(rows)
    table_chunks = [
        chunk for chunk in split_markdown(md, chunk_size=120) if chunk["type"] == "table"
    ]
    assert len(table_chunks) > 1
    for chunk in table_chunks:
        assert chunk["content"].startswith("## 产品参数表\n| 产品 | 数据 |\n|---|---|\n")
        assert chunk["content"].count("|---|---|") == 1, "分隔行不得重复"


def test_long_section_only_later_text_chunks_get_heading():
    """同一章节内第 1 段本就含标题，第 2 段及以后才补标题。"""
    body = "\n".join(f"第{i}行：" + "这是一段很长的文本内容。" * 8 for i in range(60))
    md = "## 详细说明\n" + body
    chunks = split_markdown(md, chunk_size=200, chunk_overlap=20)
    assert len(chunks) >= 2
    assert chunks[0]["content"].lstrip().startswith("## 详细说明")
    assert not chunks[0]["content"].startswith("## 详细说明\n## 详细说明")
    for chunk in chunks[1:]:
        assert chunk["content"].startswith("## 详细说明\n")


def test_headingless_document_uses_neighbour_blocks_as_evidence_unit():
    """无标题文档：证据单元退化为「命中块 + 前后各一个相邻块」。"""
    md = "第一段文本。\n\n| A | B |\n|---|---|\n| 1 | 2 |\n最后一段文本。"
    chunks = split_markdown(md)
    text_chunks = [chunk for chunk in chunks if chunk["type"] == "text"]
    assert len(text_chunks) == 2
    assert all(chunk["parent_type"] == "section" for chunk in text_chunks)
    assert (text_chunks[0]["parent_start_line"], text_chunks[0]["parent_end_line"]) == (1, 5)
    assert (text_chunks[1]["parent_start_line"], text_chunks[1]["parent_end_line"]) == (3, 6)


def test_text_block_after_blank_line_belongs_to_its_own_section():
    """表格后的空行会让文本块的首行是空行，章节归属仍须落在自己的标题上。"""
    md = (
        "## 第一节\n正文一。\n"
        "| A | B |\n|---|---|\n| 1 | 2 |\n"
        "\n"
        "## 第二节\n正文二。"
    )
    chunks = split_markdown(md)
    last = chunks[-1]
    assert last["type"] == "text"
    assert last["parent_type"] == "section"
    assert (last["parent_start_line"], last["parent_end_line"]) == (7, 8)
    # 该块本身就含标题，不应再被当作「第 2 段」重复补一次标题
    assert last["content"].count("## 第二节") == 1


def test_evidence_unit_expansion_returns_whole_table_from_any_segment():
    rows = [f"| 产品{i} | 数据{i} |" for i in range(40)]
    md = "## 参数表\n| 产品 | 数据 |\n|---|---|\n" + "\n".join(rows)
    doc_id = create_document("参数表.md", "product", source_content=md)
    table_chunks = [
        chunk for chunk in split_markdown(md, chunk_size=120) if chunk["type"] == "table"
    ]
    assert len(table_chunks) > 1

    expected_table = md.split("\n", 1)[1]
    keys = set()
    for chunk in table_chunks:
        expanded = expand_evidence_unit(
            doc_id,
            chunk["parent_start_line"],
            chunk["parent_end_line"],
            chunk["start_line"],
        )
        assert expanded == expected_table
        keys.add(evidence_unit_key(doc_id, chunk["parent_start_line"], chunk["parent_end_line"]))
    assert len(keys) == 1, "同一张表的多个分段必须归为同一个证据单元"


def test_evidence_unit_expansion_truncates_around_hit():
    md = "\n".join(f"第{i}行的内容" for i in range(1, 101))
    doc_id = create_document("长文档.md", "common", source_content=md)
    text = expand_evidence_unit(doc_id, 1, 100, hit_start_line=80, max_chars=100)
    assert "第80行的内容" in text
    assert "第1行的内容" not in text
    assert len(text) <= 100


def test_evidence_unit_expansion_missing_document():
    assert expand_evidence_unit(9999, 1, 10, 1) == ""
