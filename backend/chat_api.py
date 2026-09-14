"""问答接口：会话上下文 + 非流式 / SSE（设计文档 6.2 / 6.9 / 7.5）。

消息落库与状态机：
- 用户消息在请求校验通过后立即写入；
- 助手消息在流开始时先落一条 streaming，其 id 通过首个 SSE 事件返回前端；
- 流正常结束置 completed，生成异常置 failed，客户端中断（生成器被关闭）置 aborted，
  三种情况都保留已产出的部分内容与来源。
"""

import json

from flask import Blueprint, Response, g, jsonify, request, stream_with_context

from auth import require_auth
from config import Config
from conversations_api import owned_conversation
from errors import api_error
import llm
import orchestrator
from models import (
    create_message,
    list_messages,
    recent_context_messages,
    touch_conversation,
    update_conversation_title,
    update_message,
)
from rag import answer_question
from reranker import RerankerError

chat_bp = Blueprint("chat", __name__, url_prefix="/api")

SUPPORTED_MODES = ("standard", "enhanced")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _finalize(
    message_id: int,
    conversation_id: int,
    parts: list[str],
    sources: list,
    status: str,
    trace: dict | None = None,
) -> None:
    update_message(
        message_id,
        content="".join(parts),
        status=status,
        sources_json=json.dumps(sources, ensure_ascii=False),
        trace_json=json.dumps(trace, ensure_ascii=False) if trace else None,
    )
    touch_conversation(conversation_id)


@chat_bp.post("/chat")
@require_auth
def chat():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return api_error("缺少问题", "BAD_REQUEST", 400)

    conversation_id = data.get("conversation_id")
    if not isinstance(conversation_id, int) or isinstance(conversation_id, bool):
        return api_error("缺少或非法 conversation_id", "BAD_REQUEST", 400)
    conversation = owned_conversation(conversation_id)
    if conversation is None:
        return api_error("会话不存在", "NOT_FOUND", 404)

    mode = (data.get("mode") or "standard").strip() or "standard"
    if mode not in SUPPORTED_MODES:
        return api_error(f"暂不支持的模式：{mode}", "MODE_UNAVAILABLE", 400)

    stream = bool(data.get("stream", False))
    if mode == "enhanced":
        if not llm.get_active_provider().agent_capable:
            return api_error(
                "当前模型不支持增强模式", "MODE_UNAVAILABLE", 400
            )
        if not stream:
            return api_error(
                "增强模式仅支持流式调用", "MODE_REQUIRES_STREAM", 400
            )

    # 上下文由服务端从会话消息中截取，不再接收前端 history
    history = recent_context_messages(conversation_id, Config.CHAT_HISTORY_TURNS)
    create_message(conversation_id, "user", question, mode=mode)
    if not conversation["title"]:
        update_conversation_title(conversation_id, question[:20])
    touch_conversation(conversation_id)

    trace: dict | None = None
    try:
        if mode == "enhanced":
            trace = {}
            result = orchestrator.run_enhanced(
                question, g.user["role"], history, trace
            )
        else:
            result = answer_question(
                question,
                history=history,
                user_role=g.user["role"],
                stream=stream,
            )
    except RerankerError as exc:
        create_message(conversation_id, "assistant", "", mode=mode, status="failed")
        return api_error(str(exc) or "重排服务异常", "RERANKER_ERROR", 500)
    except Exception as exc:  # noqa: BLE001 - 兜底保证统一错误格式
        create_message(conversation_id, "assistant", "", mode=mode, status="failed")
        return api_error(str(exc) or "服务器内部错误", "INTERNAL_ERROR", 500)

    if not stream:
        message_id = create_message(
            conversation_id,
            "assistant",
            result.get("answer", ""),
            mode=mode,
            status="completed",
            sources_json=json.dumps(result.get("sources") or [], ensure_ascii=False),
        )
        return jsonify({**result, "message_id": message_id})

    message_id = create_message(
        conversation_id, "assistant", "", mode=mode, status="streaming"
    )

    def generate():
        parts: list[str] = []
        sources: list = []
        status = "aborted"
        yield _sse({"message_id": message_id, "mode": mode})
        try:
            for event in result:
                payload = json.loads(event) if event.strip().startswith("{") else {}
                if "token" in payload:
                    parts.append(payload["token"])
                elif "sources" in payload:
                    sources = payload.get("sources") or []
                yield f"data: {event}\n\n"
            status = "completed"
        except Exception:  # noqa: BLE001 - 流式中途失败以 error 事件告知前端
            status = "failed"
            yield _sse({"error": "生成失败，请稍后重试"})
        finally:
            _finalize(message_id, conversation_id, parts, sources, status, trace)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
