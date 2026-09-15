"""JWT 认证、密码哈希与角色鉴权装饰器。"""

import time
from functools import wraps

import jwt
from flask import Blueprint, g, jsonify, request
from werkzeug.security import check_password_hash

from config import Config
from errors import api_error
from models import (
    get_user_by_id,
    get_user_by_username,
    reset_user_password,
)

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def create_token(
    user_id: int, username: str, role: str, token_version: int
) -> str:
    """签发 JWT；密钥长度不足时抛 RuntimeError。"""
    if len(Config.SECRET_KEY) < 32:
        raise RuntimeError("JWT_SECRET_KEY 未配置或长度不足 32 字符")
    payload = {
        "user_id": user_id,
        "username": username,
        "role": role,
        "ver": token_version,
        "exp": int(time.time()) + Config.JWT_EXPIRATION_HOURS * 3600,
    }
    return jwt.encode(payload, Config.SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> dict:
    """解码并校验 JWT（签名 + exp）；失败抛 jwt.PyJWTError，由调用方转成 401。"""
    return jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])


def require_auth(f):
    """认证装饰器：校验 Bearer token 并把当前用户放进 `g.user`。

    三类失败都返回 401，但 code 不同，便于前端区分处理：
    UNAUTHORIZED（无/坏 token、用户已不存在）、ACCOUNT_DISABLED（账号被停用）、
    TOKEN_STALE（改密/重置密码后 token_version 变化导致旧 token 失效）。
    """

    @wraps(f)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return api_error("未认证", "UNAUTHORIZED", 401)
        token = header[7:].strip()
        try:
            payload = decode_token(token)
        except jwt.PyJWTError:
            return api_error("登录已过期或 token 无效", "UNAUTHORIZED", 401)
        # 用户信息每次请求都现查数据库：停用、删除、改角色即时生效，不依赖 token 内容
        user = get_user_by_id(payload["user_id"])
        if user is None:
            return api_error("用户不存在", "UNAUTHORIZED", 401)
        if user.get("is_active") == 0:
            return api_error("账号已停用", "ACCOUNT_DISABLED", 401)
        if payload.get("ver", 0) != user.get("token_version", 0):
            return api_error("登录已失效，请重新登录", "TOKEN_STALE", 401)
        g.user = user
        return f(*args, **kwargs)

    return wrapper


def require_admin(f):
    """管理员装饰器：必须叠加在 `@require_auth` 之内（依赖 g.user 已就绪）。"""

    @wraps(f)
    def wrapper(*args, **kwargs):
        if g.user.get("role") != "admin":
            return api_error("无权限执行此操作", "FORBIDDEN", 403)
        return f(*args, **kwargs)

    return wrapper


@auth_bp.post("/login")
def login():
    """登录：校验凭据并签发 JWT。

    用户名不存在与密码错误返回同一句话（不暴露账号是否存在）；被停用账号单独
    返回 403，让用户知道要找管理员而不是反复试密码。
    """
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = get_user_by_username(username)
    if user is None or not check_password_hash(user["password_hash"], password):
        return api_error("用户名或密码错误", "INVALID_CREDENTIALS", 401)
    if user.get("is_active") == 0:
        return api_error("账号已停用", "ACCOUNT_DISABLED", 403)
    token = create_token(
        user["id"], user["username"], user["role"], user.get("token_version", 0)
    )
    return jsonify(
        {
            "token": token,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "role": user["role"],
            },
        }
    )


@auth_bp.get("/me")
@require_auth
def me():
    """返回当前登录用户的身份信息（前端据此渲染导航与角色相关入口）。"""
    return jsonify(
        {
            "id": g.user["id"],
            "username": g.user["username"],
            "role": g.user["role"],
        }
    )


@auth_bp.put("/password")
@require_auth
def change_password():
    """自助修改密码（所有登录用户）：验证当前密码；成功后 token_version
    自增使全部旧会话失效，并返回新签发的 token 供当前会话无缝续用。"""
    data = request.get_json(silent=True) or {}
    old_password = data.get("old_password") or ""
    new_password = data.get("new_password") or ""
    if len(new_password) < 6:
        return api_error("新密码长度不能少于 6 位", "BAD_REQUEST", 400)

    user = get_user_by_username(g.user["username"])
    if user is None or not check_password_hash(user["password_hash"], old_password):
        return api_error("当前密码错误", "WRONG_PASSWORD", 400)

    reset_user_password(user["id"], new_password)
    refreshed = get_user_by_id(user["id"])
    token = create_token(
        refreshed["id"],
        refreshed["username"],
        refreshed["role"],
        refreshed["token_version"],
    )
    return jsonify({"token": token})
