"""模型设置接口（仅 admin）：查看预设提供方、切换当前模型、测试连通性。"""

from flask import Blueprint, jsonify, request

import llm
from auth import require_admin, require_auth
from errors import api_error
from models import set_setting

settings_bp = Blueprint("settings", __name__, url_prefix="/api/settings")


def _settings_payload() -> dict:
    active = llm.get_active_provider()
    return {
        "active": active.id,
        "default": llm.DEFAULT_PROVIDER_ID,
        "providers": [
            {
                "id": provider.id,
                "name": provider.name,
                "platform": provider.platform,
                "base_url": provider.base_url,
                "model": provider.model,
                "api_key_configured": provider.configured,
                "active": provider.id == active.id,
            }
            for provider in llm.list_providers()
        ],
    }


def _require_valid_provider(provider_id: str):
    """校验提供方存在且密钥已配置；失败返回错误响应，成功返回 None。"""
    provider = llm.get_provider(provider_id)
    if provider is None:
        return api_error("模型提供方不存在", "NOT_FOUND", 404)
    if not provider.configured:
        return api_error(
            f"{provider.name} 的 API Key 未配置，无法切换", "PROVIDER_KEY_MISSING", 400
        )
    return None


@settings_bp.get("/model")
@require_auth
@require_admin
def get_model_settings():
    return jsonify(_settings_payload())


@settings_bp.put("/model")
@require_auth
@require_admin
def switch_model():
    data = request.get_json(silent=True) or {}
    provider_id = (data.get("provider_id") or "").strip()
    error = _require_valid_provider(provider_id)
    if error is not None:
        return error
    set_setting(llm.SETTING_KEY, provider_id)
    return jsonify(_settings_payload())


@settings_bp.post("/model/test")
@require_auth
@require_admin
def test_model():
    data = request.get_json(silent=True) or {}
    provider_id = (data.get("provider_id") or "").strip()
    error = _require_valid_provider(provider_id)
    if error is not None:
        return error
    provider = llm.get_provider(provider_id)
    try:
        reply = llm.test_provider(provider)
    except Exception as exc:  # noqa: BLE001 - 测试失败原因需透出给管理员
        return api_error(f"连接失败：{exc}", "PROVIDER_TEST_FAILED", 502)
    return jsonify(
        {"ok": True, "provider_id": provider.id, "model": provider.model, "reply": reply}
    )
