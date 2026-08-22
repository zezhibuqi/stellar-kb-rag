"""问答接口：非流式 + SSE 流式（设计文档 6.2 / 7.5）。"""

import json

from flask import Blueprint, Response, g, jsonify, request, stream_with_context

from auth import require_auth
from errors import api_error
from rag import answer_question
from reranker import RerankerError

chat_bp = Blueprint("chat", __name__, url_prefix="/api")


@chat_bp.post("/chat")
@require_auth
def chat():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return api_error("缺少问题", "BAD_REQUEST", 400)
    history = data.get("history") or []
    if not isinstance(history, list):
        return api_error("history 必须为数组", "BAD_REQUEST", 400)
    stream = bool(data.get("stream", False))

    try:
        result = answer_question(
            question,
            history=history,
            user_role=g.user["role"],
            stream=stream,
        )
    except RerankerError as exc:
        return api_error(str(exc) or "重排服务异常", "RERANKER_ERROR", 500)
    except Exception as exc:  # noqa: BLE001 - 兜底保证统一错误格式
        return api_error(str(exc) or "服务器内部错误", "INTERNAL_ERROR", 500)

    if stream:
        def generate():
            try:
                for event in result:
                    yield f"data: {event}\n\n"
            except Exception:  # noqa: BLE001 - 流式中途失败以 error 事件告知前端
                error_event = json.dumps(
                    {"error": "生成失败，请稍后重试"}, ensure_ascii=False
                )
                yield f"data: {error_event}\n\n"

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return jsonify(result)
