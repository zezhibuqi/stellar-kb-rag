"""Markdown 切片器（设计文档 7.1 / ADR 0009）。

- 文本区用 RecursiveCharacterTextSplitter 切分，表格区整块保留，超长表格按「表头 + 数据段」拆分；
- 表格块拼「最近标题 + 表格上方最近的非空非表格行」前缀，文本块仅第 2 段及以后补最近标题；
- 前缀不计入 chunk_size 预算，但会随 content 一起向量化；start_line 仍指向原内容首行；
- 每个切块携带证据单元行区间（parent_type / parent_start_line / parent_end_line）。
"""

import logging
import re

from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import Config

logger = logging.getLogger("splitter")

_SEPARATOR_RE = re.compile(r"^\s*\|[\s:\-|]+\|\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*)$")

# 表格上方最多回看的行数（含空行），用于取表名/单位口径那一行
_CAPTION_LOOKBACK_LINES = 3


def split_markdown(
    md_content: str,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[dict]:
    """按文本/表格区块切分 Markdown，返回带前缀与证据单元行区间的 chunk 列表。"""
    chunk_size = chunk_size or Config.MAX_CHUNK_SIZE
    chunk_overlap = chunk_overlap or Config.MAX_CHUNK_OVERLAP

    lines = md_content.split("\n")
    blocks = _scan_blocks(lines)
    headings = _scan_headings(lines)
    _annotate_blocks(blocks, headings, lines)
    return _process_blocks(blocks, chunk_size, chunk_overlap)


def _scan_blocks(lines: list[str]) -> list[dict]:
    """逐行扫描，把连续同类行归为文本块或表格块。"""
    blocks: list[dict] = []
    current: dict | None = None
    for line_no, line in enumerate(lines, start=1):
        block_type = "table" if line.strip().startswith("|") else "text"
        if current is None or current["type"] != block_type:
            current = {
                "type": block_type,
                "start_line": line_no,
                "end_line": line_no,
                "lines": [line],
            }
            blocks.append(current)
        else:
            current["end_line"] = line_no
            current["lines"].append(line)
    for block in blocks:
        block["content"] = "\n".join(block["lines"])
    return blocks


def _scan_headings(lines: list[str]) -> list[dict]:
    """收集标题行（层级 + 行号 + 原文），用于前缀与章节边界。"""
    headings: list[dict] = []
    for line_no, line in enumerate(lines, start=1):
        match = _HEADING_RE.match(line.strip())
        if match:
            headings.append(
                {
                    "line": line_no,
                    "level": len(match.group(1)),
                    "text": match.group(0),
                }
            )
    return headings


def _last_heading_index(headings: list[dict], line_no: int) -> int:
    """返回该行之前（含该行）的最近标题下标，没有则 -1。"""
    result = -1
    for index, heading in enumerate(headings):
        if heading["line"] <= line_no:
            result = index
        else:
            break
    return result


def _first_meaningful_line(block: dict) -> int:
    """块内首个非空行的行号。

    文本块的首行常常是表格之后的空行，若直接用块起始行定位章节，会把该块
    错判成上一节的一部分；因此章节归属以首个非空行为锚点。
    """
    for offset, line in enumerate(block["lines"]):
        if line.strip():
            return block["start_line"] + offset
    return block["start_line"]


def _section_end(headings: list[dict], heading_index: int, total_lines: int) -> int:
    """章节结束行：下一个同级或更高级标题之前；没有则到文档末尾。"""
    level = headings[heading_index]["level"]
    for heading in headings[heading_index + 1 :]:
        if heading["level"] <= level:
            return heading["line"] - 1
    return total_lines


def _caption_above(block: dict, lines: list[str]) -> str | None:
    """表格上方最近的非空非表格行；该行本身是标题时返回 None（标题由前缀单独承担）。"""
    position = block["start_line"] - 2  # 表格上一行的 0-based 下标
    examined = 0
    while position >= 0 and examined < _CAPTION_LOOKBACK_LINES:
        text = lines[position].strip()
        position -= 1
        examined += 1
        if not text or text.startswith("|"):
            continue
        if _HEADING_RE.match(text):
            return None
        return text
    return None


def _annotate_blocks(blocks: list[dict], headings: list[dict], lines: list[str]) -> None:
    """为每个块补上最近标题、表格说明行与证据单元行区间。"""
    total_lines = len(lines)
    for index, block in enumerate(blocks):
        heading_index = _last_heading_index(headings, _first_meaningful_line(block))
        block["heading_index"] = heading_index
        block["heading"] = headings[heading_index] if heading_index >= 0 else None

        if block["type"] == "table":
            # 证据单元 = 整张表
            block["caption"] = _caption_above(block, lines)
            block["parent_type"] = "table"
            block["parent_start_line"] = block["start_line"]
            block["parent_end_line"] = block["end_line"]
            continue

        # 证据单元 = 所属章节；无标题时退化为「命中块 + 前后各一个相邻块」
        block["caption"] = None
        block["parent_type"] = "section"
        if heading_index >= 0:
            start = headings[heading_index]["line"]
            end = _section_end(headings, heading_index, total_lines)
        else:
            start = blocks[index - 1]["start_line"] if index > 0 else block["start_line"]
            end = (
                blocks[index + 1]["end_line"]
                if index + 1 < len(blocks)
                else block["end_line"]
            )
        block["parent_start_line"] = start
        block["parent_end_line"] = end


def _table_prefix(block: dict) -> str:
    """表格块前缀：最近标题 + 表格上方最近的非空非表格行。"""
    parts = []
    if block["heading"]:
        parts.append(block["heading"]["text"])
    if block["caption"]:
        parts.append(block["caption"])
    return "\n".join(parts)


def _process_blocks(
    blocks: list[dict], chunk_size: int, chunk_overlap: int
) -> list[dict]:
    """把标注好的块切成最终 chunk 列表，并补上下文前缀与证据单元行区间。

    文本块与表格块的区别：文本块第 2 段起补最近标题（第 1 段本就含标题）；
    表格块每一段（含超长表拆出的多段）都要补「标题 + 表名/单位」前缀，
    因为每段都必须能独立被检索到。
    """
    result: list[dict] = []
    section_text_seen: dict[int, int] = {}

    for block in blocks:
        if block["type"] == "text":
            parts = _split_text(
                block["content"], chunk_size, chunk_overlap, block["start_line"]
            )
            key = block["heading_index"]
            for part in parts:
                seen = section_text_seen.get(key, 0)
                section_text_seen[key] = seen + 1
                # 同一章节内第 1 段本就含标题，第 2 段及以后补最近标题
                if seen > 0 and block["heading"]:
                    part["content"] = f"{block['heading']['text']}\n{part['content']}"
        else:
            parts = _split_table(block["content"], chunk_size, block["start_line"])
            prefix = _table_prefix(block)
            if prefix:
                for part in parts:
                    part["content"] = f"{prefix}\n{part['content']}"

        for part in parts:
            part["parent_type"] = block["parent_type"]
            part["parent_start_line"] = block["parent_start_line"]
            part["parent_end_line"] = block["parent_end_line"]
            result.append(part)

    return result


def _split_text(
    content: str, chunk_size: int, chunk_overlap: int, block_start_line: int
) -> list[dict]:
    """按 chunk_size/overlap 切文本，并把块内偏移换算回原文行号（1-indexed）。

    行号换算用「上一段结束位置 - overlap」作为查找起点，兼容重叠切块时文本
    在块内重复出现的情况，避免定位漂移。
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    result: list[dict] = []
    pos = 0
    for part in splitter.split_text(content):
        # 从“上一段结束位置 - overlap”开始查找，兼容重叠切块
        idx = content.find(part, max(0, pos - chunk_overlap))
        if idx < 0:
            idx = content.find(part)
        if idx < 0:
            idx = 0
        pos = idx + len(part)
        if part.strip():
            start_line = block_start_line + content[:idx].count("\n")
            result.append({"type": "text", "content": part, "start_line": start_line})
    return result


def _split_table(content: str, chunk_size: int, block_start_line: int) -> list[dict]:
    """切表格：不超过 chunk_size 整块保留；超限则每段都带完整表头（含分隔行）。

    表头本身超过 chunk_size 时放弃切分并记警告——没有表头的表格片段会失去
    列语义，宁可让这一块超长。
    """
    if len(content) <= chunk_size:
        return [{"type": "table", "content": content, "start_line": block_start_line}]

    lines = content.split("\n")
    header_block = [lines[0]]
    if len(lines) > 1 and _SEPARATOR_RE.match(lines[1]):
        header_block.append(lines[1])
    data_lines = lines[len(header_block) :]
    header_text = "\n".join(header_block) + "\n"

    if len(header_text) > chunk_size:
        logger.warning("表头超过 %s 字符，整块保存：%s", chunk_size, lines[0][:80])
        return [{"type": "table", "content": content, "start_line": block_start_line}]

    segments: list[dict] = []
    current: list[str] = []
    current_len = len(header_text)
    for row in data_lines:
        row_len = len(row) + 1
        if current and current_len + row_len > chunk_size:
            segments.append(
                {
                    "type": "table",
                    "content": header_text + "\n".join(current),
                    "start_line": block_start_line,
                }
            )
            current = []
            current_len = len(header_text)
        current.append(row)
        current_len += row_len
    if current:
        segments.append(
            {
                "type": "table",
                "content": header_text + "\n".join(current),
                "start_line": block_start_line,
            }
        )
    return segments
