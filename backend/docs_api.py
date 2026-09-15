"""知识库管理接口（仅 admin）：上传、列表、状态、删除、原文。"""

from pathlib import Path

from flask import Blueprint, g, jsonify, request

import tasks
from auth import require_admin, require_auth
from errors import api_error
from models import (
    create_document,
    delete_document,
    get_allowed_domains,
    get_document,
    list_documents,
)

docs_bp = Blueprint("docs", __name__, url_prefix="/api")


def _decode_markdown_bytes(data: bytes) -> str:
    """按 UTF-8 优先、GB18030 兜底解码上传文件（Windows 用户常见 GBK 编码）。"""
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


@docs_bp.get("/docs")
@require_auth
@require_admin
def list_docs():
    """文档列表（仅 admin），可按 domain 过滤；不返回全文，避免响应体过大。"""
    domain = (request.args.get("domain") or "").strip() or None
    rows = list_documents(domain)
    return jsonify(
        [
            {
                "id": doc["id"],
                "filename": doc["filename"],
                "domain": doc["domain_name"],
                "chunk_count": doc["chunk_count"],
                "status": doc["status"],
                "uploaded_at": doc["uploaded_at"],
            }
            for doc in rows
        ]
    )


@docs_bp.post("/upload")
@require_auth
@require_admin
def upload_doc():
    """上传 Markdown：先落 documents 记录（pending）再提交后台灌库，立即返回 doc_id。

    同步只做「解码 + 入库 + 提交任务」，耗时的向量化放在线程池里跑，避免请求超时；
    前端通过 /docs/:id/status 轮询进度。
    """
    file = request.files.get("file")
    domain = (request.form.get("domain") or "").strip()
    if file is None or not file.filename:
        return api_error("缺少文件", "BAD_REQUEST", 400)

    filename = Path(file.filename).name
    if not filename.lower().endswith(".md"):
        return api_error("仅支持 .md 文件", "BAD_REQUEST", 400)
    if not domain:
        return api_error("缺少领域", "BAD_REQUEST", 400)

    content_bytes = file.read()
    if len(content_bytes) > 10 * 1024 * 1024:
        return api_error(
            "文件超过 10MB 上限", "FILE_TOO_LARGE", 400
        )

    try:
        content = _decode_markdown_bytes(content_bytes)
        doc_id = create_document(
            filename,
            domain,
            uploaded_by=g.user["id"],
            source_content=content,
        )
    except ValueError as exc:
        return api_error(str(exc), "BAD_REQUEST", 400)

    tasks.submit_processing(doc_id)
    return jsonify({"doc_id": doc_id, "status": "pending"}), 202


@docs_bp.get("/docs/<int:doc_id>/status")
@require_auth
@require_admin
def doc_status(doc_id: int):
    """查询灌库进度；失败时附带 error 字段（前端直接展示失败原因）。"""
    doc = get_document(doc_id)
    if doc is None:
        return api_error("文档不存在", "NOT_FOUND", 404)
    response = {
        "doc_id": doc_id,
        "status": doc["status"],
        "chunk_count": doc["chunk_count"],
    }
    if doc["error_message"]:
        response["error"] = doc["error_message"]
    return jsonify(response)


@docs_bp.delete("/docs/<int:doc_id>")
@require_auth
@require_admin
def delete_doc(doc_id: int):
    """删除文档：按顺序清理 Chroma 向量 → 关键词索引 → SQLite 记录。

    三处必须同步清理：漏掉关键词索引会留下「幽灵命中」（检索到已删除文档的块）。
    """
    doc = get_document(doc_id)
    if doc is None:
        return api_error("文档不存在", "NOT_FOUND", 404)

    import chroma_store
    import keyword_index

    chroma_store.delete_by_doc_id(doc_id)
    keyword_index.delete_document(doc_id)
    delete_document(doc_id)

    return jsonify({"message": "文档已删除"})


@docs_bp.get("/docs/<int:doc_id>/raw")
@require_auth
def doc_raw(doc_id: int):
    """返回原文档全文（Markdown）：所有登录用户都可请求，但按角色领域校验。

    越权返回 403 而非 404 —— 引用来源里已经出现文件名，没有必要隐藏文档是否存在。
    """
    doc = get_document(doc_id)
    if doc is None:
        return api_error("文档不存在", "NOT_FOUND", 404)
    allowed_domains = get_allowed_domains(g.user["role"])
    if doc["domain_name"] not in allowed_domains:
        return api_error("无权限查看该文档", "FORBIDDEN", 403)
    return jsonify(
        {
            "filename": doc["filename"],
            "domain": doc["domain_name"],
            "content": doc.get("source_content") or "",
        }
    )
