"""用户管理接口（仅 admin）：创建、列表、修改角色。"""

from flask import Blueprint, g, jsonify, request

from auth import require_admin, require_auth
from errors import api_error
from models import (
    ROLE_VALUES,
    activate_user,
    count_active_admins,
    count_admins,
    create_user,
    deactivate_user,
    get_user_by_id,
    list_users,
    reset_user_password,
    update_user_role,
)

users_bp = Blueprint("users", __name__, url_prefix="/api/users")


@users_bp.get("")
@require_auth
@require_admin
def list_users_api():
    return jsonify(list_users())


@users_bp.post("")
@require_auth
@require_admin
def create_user_api():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    display_name = data.get("display_name")
    role = data.get("role") or "employee"
    try:
        user_id = create_user(username, password, display_name=display_name, role=role)
    except ValueError as exc:
        return api_error(str(exc), "BAD_REQUEST", 400)
    user = get_user_by_id(user_id)
    return jsonify(
        {"id": user["id"], "username": user["username"], "role": user["role"]}
    ), 201


@users_bp.put("/<int:user_id>/role")
@require_auth
@require_admin
def update_role_api(user_id: int):
    data = request.get_json(silent=True) or {}
    new_role = data.get("role")
    if new_role not in ROLE_VALUES:
        return api_error("非法角色", "BAD_REQUEST", 400)
    target = get_user_by_id(user_id)
    if target is None:
        return api_error("用户不存在", "NOT_FOUND", 404)
    if target["role"] == "admin" and new_role != "admin" and count_admins() <= 1:
        return api_error("不能将最后一个 admin 降级", "LAST_ADMIN", 400)
    if target["id"] == g.user["id"]:
        return api_error("admin 不能修改自己的角色", "ADMIN_SELF_ROLE", 403)
    update_user_role(user_id, new_role)
    return jsonify({"id": target["id"], "role": new_role})


@users_bp.delete("/<int:user_id>")
@require_auth
@require_admin
def delete_user_api(user_id: int):
    target = get_user_by_id(user_id)
    if target is None:
        return api_error("用户不存在", "NOT_FOUND", 404)
    if target["id"] == g.user["id"]:
        return api_error("admin 不能删除自己", "ADMIN_SELF_DELETE", 403)
    if (
        target.get("is_active") == 1
        and target["role"] == "admin"
        and count_active_admins() <= 1
    ):
        return api_error("不能停用最后一个可用 admin", "LAST_ADMIN", 400)
    deactivate_user(user_id)
    return jsonify({"id": target["id"], "is_active": False})


@users_bp.put("/<int:user_id>/active")
@require_auth
@require_admin
def activate_user_api(user_id: int):
    data = request.get_json(silent=True) or {}
    if data.get("is_active") is not True:
        return api_error("仅支持恢复启用（is_active=true）", "BAD_REQUEST", 400)
    target = get_user_by_id(user_id)
    if target is None:
        return api_error("用户不存在", "NOT_FOUND", 404)
    activate_user(user_id)
    return jsonify({"id": target["id"], "is_active": True})


@users_bp.put("/<int:user_id>/password")
@require_auth
@require_admin
def reset_password_api(user_id: int):
    data = request.get_json(silent=True) or {}
    new_password = data.get("new_password") or ""
    target = get_user_by_id(user_id)
    if target is None:
        return api_error("用户不存在", "NOT_FOUND", 404)
    try:
        reset_user_password(user_id, new_password)
    except ValueError as exc:
        return api_error(str(exc), "BAD_REQUEST", 400)
    return jsonify({"id": target["id"], "message": "密码已重置，请重新登录"})
