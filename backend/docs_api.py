"""知识库管理接口（仅 admin）：上传、列表、状态、删除。"""

from pathlib import Path

from flask import Blueprint, g, jsonify, request

import tasks
import chroma_store
from auth import require_admin, require_auth
from config import Config
from errors import api_error
from models import create_document, delete_document, get_document, list_documents

docs_bp = Blueprint("docs", __name__, url_prefix="/api")

UPLOAD_ROOT = Path(Config.DATABASE_URL).parent / "uploads"
MAX_UPLOAD_BYTES = Config.UPLOAD_MAX_SIZE_MB * 1024 * 1024


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

    content = file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        return api_error(
            f"文件超过 {Config.UPLOAD_MAX_SIZE_MB}MB 上限", "FILE_TOO_LARGE", 400
        )

    try:
        doc_id = create_document(filename, domain, uploaded_by=g.user["id"])
    except ValueError as exc:
        return api_error(str(exc), "BAD_REQUEST", 400)

    domain_dir = UPLOAD_ROOT / domain
    domain_dir.mkdir(parents=True, exist_ok=True)
    saved_path = domain_dir / f"{doc_id}_{filename}"
    saved_path.write_bytes(content)

    tasks.submit_processing(doc_id, str(saved_path))
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

    chroma_store.delete_by_doc_id(doc_id)
    delete_document(doc_id)

    # 清理暂存的上传文件（运行时数据，可恢复性由用户重新上传保证）
    try:
        saved_path = UPLOAD_ROOT / doc["domain_name"] / f"{doc_id}_{doc['filename']}"
        saved_path.unlink(missing_ok=True)
    except OSError:
        pass

    return jsonify({"message": "文档已删除"})
