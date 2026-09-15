"""关键词索引基础设施测试（ADR 0010）：表随 init_db 创建、trigram 中文子串可命中。"""

from models import get_connection


def test_chunk_index_created_and_trigram_search_hits_chinese():
    """FTS5 + trigram 建表生效：中文子串（回收量）能 MATCH 到包含它的整句。"""
    conn = get_connection()
    conn.execute(
        "INSERT INTO chunk_index (content, doc_id, chunk_id, domain, filename, "
        "start_line, parent_type, parent_start_line, parent_end_line) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "公司废旧电池及材料综合回收量达到 21万吨，同比增长超六成",
            1,
            0,
            "finance",
            "2025年度报告.md",
            26,
            "section",
            1,
            30,
        ),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT doc_id, start_line, domain FROM chunk_index "
        "WHERE chunk_index MATCH ? ORDER BY bm25(chunk_index) LIMIT 5",
        ("回收量",),
    ).fetchall()
    assert [(row["doc_id"], row["start_line"], row["domain"]) for row in rows] == [
        (1, 26, "finance")
    ]


def test_missing_keyword_index_can_be_detected():
    """回填脚本据此只处理缺失的文档（幂等、可重跑）。"""
    conn = get_connection()
    missing = conn.execute(
        "SELECT COUNT(*) AS c FROM documents WHERE keyword_indexed_at IS NULL"
    ).fetchone()["c"]
    assert missing == 0, "种子环境没有文档"


def test_documents_have_keyword_indexed_at_column():
    """documents 表有 keyword_indexed_at 列，回填与健康检查依赖它判断缺失。"""
    conn = get_connection()
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(documents)").fetchall()
    }
    assert "keyword_indexed_at" in columns
