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
    doc = get_document(doc_id)
    if doc is None:
        return api_error("文档不存在", "NOT_FOUND", 404)

    import chroma_store

    chroma_store.delete_by_doc_id(doc_id)
    delete_document(doc_id)

    return jsonify({"message": "文档已删除"})


@docs_bp.get("/docs/<int:doc_id>/raw")
@require_auth
def doc_raw(doc_id: int):
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
