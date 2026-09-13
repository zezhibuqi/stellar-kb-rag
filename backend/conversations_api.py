"""会话接口（设计文档 6.9）：列表 / 新建 / 回放 / 删除，仅限本人会话。"""

import json

from flask import Blueprint, g, jsonify

from auth import require_auth
from errors import api_error
from models import (
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    list_messages,
)

conversations_bp = Blueprint("conversations", __name__, url_prefix="/api")


def owned_conversation(conversation_id: int) -> dict | None:
    """取本人会话；不存在或非本人一律返回 None，调用方按 404 处理。"""
    conversation = get_conversation(conversation_id)
    if conversation is None or conversation["user_id"] != g.user["id"]:
        return None
    return conversation


def _loads(raw: str | None):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def message_payload(message: dict) -> dict:
    """消息对外结构：把 JSON 列还原成对象，缺省用空值。"""
    return {
        "id": message["id"],
        "role": message["role"],
        "content": message["content"],
        "mode": message["mode"],
        "status": message["status"],
        "sources": _loads(message.get("sources_json")) or [],
        "trace": _loads(message.get("trace_json")),
        "created_at": message["created_at"],
    }


@conversations_bp.get("/conversations")
@require_auth
def list_my_conversations():
    return jsonify(list_conversations(g.user["id"]))


@conversations_bp.post("/conversations")
@require_auth
def create_my_conversation():
    try:
        conversation_id = create_conversation(g.user["id"])
    except ValueError as exc:
        return api_error(str(exc), "CONVERSATION_LIMIT", 400)
    return jsonify({"id": conversation_id, "title": ""}), 201


@conversations_bp.get("/conversations/<int:conversation_id>/messages")
@require_auth
def get_conversation_messages(conversation_id: int):
    if owned_conversation(conversation_id) is None:
        return api_error("会话不存在", "NOT_FOUND", 404)
    return jsonify([message_payload(row) for row in list_messages(conversation_id)])


@conversations_bp.delete("/conversations/<int:conversation_id>")
@require_auth
def delete_my_conversation(conversation_id: int):
    if owned_conversation(conversation_id) is None:
        return api_error("会话不存在", "NOT_FOUND", 404)
    delete_conversation(conversation_id)
    return jsonify({"id": conversation_id, "deleted": True})
