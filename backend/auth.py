"""JWT 认证、密码哈希与角色鉴权装饰器。"""

import time
from functools import wraps

import jwt
from flask import Blueprint, g, jsonify, request
from werkzeug.security import check_password_hash

from config import Config
from errors import api_error
from models import get_user_by_id, get_user_by_username

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
    return jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])


def require_auth(f):
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
    @wraps(f)
    def wrapper(*args, **kwargs):
        if g.user.get("role") != "admin":
            return api_error("无权限执行此操作", "FORBIDDEN", 403)
        return f(*args, **kwargs)

    return wrapper


@auth_bp.post("/login")
def login():
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
    return jsonify(
        {
            "id": g.user["id"],
            "username": g.user["username"],
            "role": g.user["role"],
        }
    )
