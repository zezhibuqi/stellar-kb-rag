"""Stage 4 切片器测试：纯文本、纯表格、混合、超长表格与表头边界。"""

import logging

from splitter import split_markdown


def test_pure_text_splits_into_text_chunks():
    content = "\n".join(f"第{i}行内容" * 20 for i in range(200))
    chunks = split_markdown(content, chunk_size=200, chunk_overlap=20)
    assert len(chunks) > 1
    assert all(chunk["type"] == "text" for chunk in chunks)


def test_pure_table_kept_whole():
    table = "| 型号 | 容量 |\n|---|---|\n| SC-100 | 100Ah |"
    chunks = split_markdown(table)
    assert chunks == [{"type": "table", "content": table}]


def test_mixed_text_and_table():
    md = "## 概述\n这是文本。\n| A | B |\n|---|---|\n| 1 | 2 |\n结尾文字。"
    chunks = split_markdown(md)
    types = [chunk["type"] for chunk in chunks]
    assert "text" in types
    assert "table" in types
    table_chunk = next(chunk for chunk in chunks if chunk["type"] == "table")
    assert table_chunk["content"].startswith("| A | B |")


def test_oversized_table_split_with_header():
    rows = [f"| 产品{i} | 数据{i} |" for i in range(30)]
    table = "| 产品 | 数据 |\n|---|---|\n" + "\n".join(rows)
    chunks = split_markdown(table, chunk_size=120)
    assert len(chunks) > 1
    assert all(chunk["type"] == "table" for chunk in chunks)
    for chunk in chunks:
        assert chunk["content"].startswith("| 产品 | 数据 |\n|---|---|\n")
        assert chunk["content"].count("|---|---|") == 1, "分隔行不得重复"


def test_oversized_header_kept_whole_with_warning(caplog):
    header = "| " + "x" * 300 + " |"
    table = header + "\n| " + "y" * 50 + " |"
    with caplog.at_level(logging.WARNING, logger="splitter"):
        chunks = split_markdown(table, chunk_size=100)
    assert len(chunks) == 1
    assert chunks[0]["content"] == table
    assert any("表头超过" in record.message for record in caplog.records)


def test_table_without_separator_line():
    table = "| 产品 | 数据 |\n| A | 1 |\n| B | 2 |"
    chunks = split_markdown(table, chunk_size=20)
    assert len(chunks) == 2
    assert all(chunk["type"] == "table" for chunk in chunks)
