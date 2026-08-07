"""Markdown 切片器（设计文档 7.1）：GFM 管道表格整块保留，超长表格按表头+数据段拆分。"""

import logging
import re

from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import Config

logger = logging.getLogger("splitter")

_SEPARATOR_RE = re.compile(r"^\s*\|[\s:\-|]+\|\s*$")


def split_markdown(
    md_content: str,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[dict]:
    """按文本/表格区块切分 Markdown，返回 [{"type": "text"|"table", "content": str}, ...]。"""
    chunk_size = chunk_size or Config.MAX_CHUNK_SIZE
    chunk_overlap = chunk_overlap or Config.MAX_CHUNK_OVERLAP

    blocks: list[dict] = []
    current_type: str | None = None
    current_lines: list[str] = []

    for line in md_content.split("\n"):
        is_table = line.strip().startswith("|")
        new_type = "table" if is_table else "text"
        if new_type != current_type and current_lines:
            blocks.append({"type": current_type, "content": "\n".join(current_lines)})
            current_lines = []
        current_type = new_type
        current_lines.append(line)
    if current_lines:
        blocks.append({"type": current_type, "content": "\n".join(current_lines)})

    return _process_blocks(blocks, chunk_size, chunk_overlap)


def _process_blocks(
    blocks: list[dict], chunk_size: int, chunk_overlap: int
) -> list[dict]:
    result: list[dict] = []
    for block in blocks:
        if block["type"] == "text":
            result.extend(_split_text(block["content"], chunk_size, chunk_overlap))
        else:
            result.extend(_split_table(block["content"], chunk_size))
    return result


def _split_text(content: str, chunk_size: int, chunk_overlap: int) -> list[dict]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    return [
        {"type": "text", "content": part}
        for part in splitter.split_text(content)
        if part.strip()
    ]


def _split_table(content: str, chunk_size: int) -> list[dict]:
    if len(content) <= chunk_size:
        return [{"type": "table", "content": content}]

    lines = content.split("\n")
    header_block = [lines[0]]
    if len(lines) > 1 and _SEPARATOR_RE.match(lines[1]):
        header_block.append(lines[1])
    data_lines = lines[len(header_block) :]
    header_text = "\n".join(header_block) + "\n"

    if len(header_text) > chunk_size:
        logger.warning("表头超过 %s 字符，整块保存：%s", chunk_size, lines[0][:80])
        return [{"type": "table", "content": content}]

    segments: list[dict] = []
    current: list[str] = []
    current_len = len(header_text)
    for row in data_lines:
        row_len = len(row) + 1
        if current and current_len + row_len > chunk_size:
            segments.append(
                {"type": "table", "content": header_text + "\n".join(current)}
            )
            current = []
            current_len = len(header_text)
        current.append(row)
        current_len += row_len
    if current:
        segments.append({"type": "table", "content": header_text + "\n".join(current)})
    return segments
