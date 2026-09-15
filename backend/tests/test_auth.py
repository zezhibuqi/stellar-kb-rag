"""Stage 2 认证与用户管理接口测试。"""

import pytest

from app import create_app
from models import create_document, get_document


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _login(client, username: str, password: str):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _admin_token(client) -> str:
    resp = _login(client, "admin", "123456")
    assert resp.status_code == 200
    return resp.get_json()["token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_register_not_exposed(client):
    resp = client.post("/api/auth/register", json={"username": "alice", "password": "secret123"})
    assert resp.status_code == 404


def test_login_success_and_me(client):
    resp = _login(client, "admin", "123456")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["user"]["role"] == "admin"
    assert isinstance(data["token"], str) and len(data["token"]) > 20

    me = client.get("/api/auth/me", headers=_auth_header(data["token"]))
    assert me.status_code == 200
    assert me.get_json() == {"id": data["user"]["id"], "username": "admin", "role": "admin"}


def test_login_wrong_password(client):
    resp = _login(client, "admin", "wrong-pass")
    assert resp.status_code == 401
    assert resp.get_json()["code"] == "INVALID_CREDENTIALS"


def test_me_without_token(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.get_json()["code"] == "UNAUTHORIZED"


def test_admin_creates_user_and_new_user_can_login(client):
    token = _admin_token(client)
    resp = client.post(
        "/api/users",
        headers=_auth_header(token),
        json={
            "username": "alice",
            "password": "secret123",
            "display_name": "Alice",
            "role": "employee",
        },
    )
    assert resp.status_code == 201
    created = resp.get_json()
    assert created["role"] == "employee"

    login = _login(client, "alice", "secret123")
    assert login.status_code == 200
    assert login.get_json()["user"]["role"] == "employee"


def test_create_user_validations(client):
    token = _admin_token(client)
    short = client.post(
        "/api/users",
        headers=_auth_header(token),
        json={"username": "bob", "password": "123"},
    )
    assert short.status_code == 400

    assert client.post(
        "/api/users",
        headers=_auth_header(token),
        json={"username": "alice", "password": "secret123", "role": "employee"},
    ).status_code == 201
    duplicate = client.post(
        "/api/users",
        headers=_auth_header(token),
        json={"username": "alice", "password": "secret123"},
    )
    assert duplicate.status_code == 400


def test_non_admin_forbidden_on_user_apis(client):
    resp = _login(client, "employee", "123456")
    token = resp.get_json()["token"]
    headers = _auth_header(token)

    assert client.get("/api/users", headers=headers).status_code == 403
    assert client.post(
        "/api/users",
        headers=headers,
        json={"username": "mallory", "password": "secret123"},
    ).status_code == 403
    assert client.put("/api/users/1/role", headers=headers, json={"role": "admin"}).status_code == 403


def test_last_admin_protection(client):
    token = _admin_token(client)
    me = client.get("/api/auth/me", headers=_auth_header(token)).get_json()
    resp = client.put(
        f"/api/users/{me['id']}/role",
        headers=_auth_header(token),
        json={"role": "employee"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "LAST_ADMIN"


def test_admin_cannot_change_own_role_when_not_last(client):
    token = _admin_token(client)
    headers = _auth_header(token)
    assert client.post(
        "/api/users",
        headers=headers,
        json={"username": "boss2", "password": "secret123", "role": "admin"},
    ).status_code == 201
    me = client.get("/api/auth/me", headers=headers).get_json()
    resp = client.put(
        f"/api/users/{me['id']}/role",
        headers=headers,
        json={"role": "employee"},
    )
    assert resp.status_code == 403
    assert resp.get_json()["code"] == "ADMIN_SELF_ROLE"


def test_admin_demotes_other_admin(client):
    token = _admin_token(client)
    headers = _auth_header(token)
    resp = client.post(
        "/api/users",
        headers=headers,
        json={"username": "boss2", "password": "secret123", "role": "admin"},
    )
    boss2_id = resp.get_json()["id"]
    demote = client.put(
        f"/api/users/{boss2_id}/role",
        headers=headers,
        json={"role": "employee"},
    )
    assert demote.status_code == 200
    assert demote.get_json()["role"] == "employee"

    users = client.get("/api/users", headers=headers).get_json()
    assert sum(1 for u in users if u["role"] == "admin") == 1


def test_disable_user_blocks_login_and_old_token(client):
    token = _admin_token(client)
    created = client.post(
        "/api/users",
        headers=_auth_header(token),
        json={"username": "victim", "password": "secret123", "role": "employee"},
    )
    victim_id = created.get_json()["id"]
    victim_token = _login(client, "victim", "secret123").get_json()["token"]

    deactivated = client.put(
        f"/api/users/{victim_id}/deactivate", headers=_auth_header(token)
    )
    assert deactivated.status_code == 200
    assert deactivated.get_json()["is_active"] is False

    blocked = _login(client, "victim", "secret123")
    assert blocked.status_code == 403
    assert blocked.get_json()["code"] == "ACCOUNT_DISABLED"
    assert client.get("/api/auth/me", headers=_auth_header(victim_token)).status_code == 401


def test_enable_user_restores_login(client):
    token = _admin_token(client)
    created = client.post(
        "/api/users",
        headers=_auth_header(token),
        json={"username": "victim", "password": "secret123", "role": "employee"},
    )
    victim_id = created.get_json()["id"]
    client.put(f"/api/users/{victim_id}/deactivate", headers=_auth_header(token))

    enabled = client.put(
        f"/api/users/{victim_id}/activate", headers=_auth_header(token)
    )
    assert enabled.status_code == 200
    assert enabled.get_json()["is_active"] is True
    assert _login(client, "victim", "secret123").status_code == 200


def test_deactivate_protections(client):
    token = _admin_token(client)
    headers = _auth_header(token)
    me = client.get("/api/auth/me", headers=headers).get_json()

    self_deactivate = client.put(f"/api/users/{me['id']}/deactivate", headers=headers)
    assert self_deactivate.status_code == 403
    assert self_deactivate.get_json()["code"] == "ADMIN_SELF_DEACTIVATE"

    boss2 = client.post(
        "/api/users",
        headers=headers,
        json={"username": "boss2", "password": "secret123", "role": "admin"},
    ).get_json()
    deactivated_boss2 = client.put(
        f"/api/users/{boss2['id']}/deactivate", headers=headers
    )
    assert deactivated_boss2.status_code == 200
    assert _login(client, "boss2", "secret123").status_code == 403


def test_delete_requires_deactivated_account(client):
    token = _admin_token(client)
    headers = _auth_header(token)
    me = client.get("/api/auth/me", headers=headers).get_json()

    self_delete = client.delete(f"/api/users/{me['id']}", headers=headers)
    assert self_delete.status_code == 403
    assert self_delete.get_json()["code"] == "ADMIN_SELF_DELETE"

    active = client.post(
        "/api/users",
        headers=headers,
        json={"username": "activeuser", "password": "secret123", "role": "employee"},
    ).get_json()
    refused = client.delete(f"/api/users/{active['id']}", headers=headers)
    assert refused.status_code == 400
    assert refused.get_json()["code"] == "NOT_DEACTIVATED"


def test_delete_user_permanently(client):
    token = _admin_token(client)
    headers = _auth_header(token)
    created = client.post(
        "/api/users",
        headers=headers,
        json={"username": "gone", "password": "secret123", "role": "employee"},
    ).get_json()
    old_token = _login(client, "gone", "secret123").get_json()["token"]
    client.put(f"/api/users/{created['id']}/deactivate", headers=headers)

    deleted = client.delete(f"/api/users/{created['id']}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.get_json()["deleted"] is True

    # 登录与旧 token 均失效
    assert _login(client, "gone", "secret123").status_code == 401
    assert client.get("/api/auth/me", headers=_auth_header(old_token)).status_code == 401
    # 记录已从列表移除
    users = client.get("/api/users", headers=headers).get_json()
    assert all(u["id"] != created["id"] for u in users)
    # 再次删除返回 404
    assert client.delete(f"/api/users/{created['id']}", headers=headers).status_code == 404


def test_username_reusable_after_real_delete(client):
    token = _admin_token(client)
    headers = _auth_header(token)
    created = client.post(
        "/api/users",
        headers=headers,
        json={"username": "reborn", "password": "secret123", "role": "employee"},
    ).get_json()
    client.put(f"/api/users/{created['id']}/deactivate", headers=headers)

    # 停用期间用户名仍被占用
    occupied = client.post(
        "/api/users",
        headers=headers,
        json={"username": "reborn", "password": "secret123"},
    )
    assert occupied.status_code == 400

    client.delete(f"/api/users/{created['id']}", headers=headers)
    recreated = client.post(
        "/api/users",
        headers=headers,
        json={"username": "reborn", "password": "secret123"},
    )
    assert recreated.status_code == 201


def test_delete_user_keeps_documents_with_null_uploader(client):
    token = _admin_token(client)
    headers = _auth_header(token)
    created = client.post(
        "/api/users",
        headers=headers,
        json={"username": "uploader", "password": "secret123", "role": "admin"},
    ).get_json()
    doc_id = create_document("Uploader's doc.md", "common", uploaded_by=created["id"])

    client.put(f"/api/users/{created['id']}/deactivate", headers=headers)
    deleted = client.delete(f"/api/users/{created['id']}", headers=headers)
    assert deleted.status_code == 200

    doc = get_document(doc_id)
    assert doc is not None
    assert doc["uploaded_by"] is None


def test_reset_password_invalidates_old_token(client):
    token = _admin_token(client)
    created = client.post(
        "/api/users",
        headers=_auth_header(token),
        json={"username": "victim", "password": "secret123", "role": "employee"},
    )
    victim_id = created.get_json()["id"]
    old_token = _login(client, "victim", "secret123").get_json()["token"]

    reset = client.put(
        f"/api/users/{victim_id}/password",
        headers=_auth_header(token),
        json={"new_password": "newpass123"},
    )
    assert reset.status_code == 200
    assert _login(client, "victim", "secret123").status_code == 401
    assert _login(client, "victim", "newpass123").status_code == 200
    assert client.get("/api/auth/me", headers=_auth_header(old_token)).status_code == 401


def test_admin_reset_own_password_invalidates_own_token(client):
    token = _admin_token(client)
    headers = _auth_header(token)
    me_id = client.get("/api/auth/me", headers=headers).get_json()["id"]

    reset = client.put(
        f"/api/users/{me_id}/password",
        headers=headers,
        json={"new_password": "adminnew123"},
    )
    assert reset.status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert _login(client, "admin", "123456").status_code == 401
    assert _login(client, "admin", "adminnew123").status_code == 200


def test_duplicate_username_after_disable(client):
    token = _admin_token(client)
    created = client.post(
        "/api/users",
        headers=_auth_header(token),
        json={"username": "ghost", "password": "secret123", "role": "employee"},
    )
    ghost_id = created.get_json()["id"]
    client.put(f"/api/users/{ghost_id}/deactivate", headers=_auth_header(token))

    duplicate = client.post(
        "/api/users",
        headers=_auth_header(token),
        json={"username": "ghost", "password": "secret123"},
    )
    assert duplicate.status_code == 400


def test_change_own_password(client):
    token = _admin_token(client)
    headers = _auth_header(token)
    resp = client.put(
        "/api/auth/password",
        headers=headers,
        json={"old_password": "123456", "new_password": "brandnew123"},
    )
    assert resp.status_code == 200
    new_token = resp.get_json()["token"]

    # 旧 token 失效，新 token 可用（当前会话无缝续用）
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get("/api/auth/me", headers=_auth_header(new_token)).status_code == 200

    # 旧密码失效、新密码可登录
    assert _login(client, "admin", "123456").status_code == 401
    assert _login(client, "admin", "brandnew123").status_code == 200


def test_non_admin_can_change_own_password(client):
    """历史问题回归：非管理员可自助修改密码。"""
    token = _admin_token(client)
    created = client.post(
        "/api/users",
        headers=_auth_header(token),
        json={"username": "selfchange", "password": "secret123", "role": "employee"},
    ).get_json()
    user_token = _login(client, "selfchange", "secret123").get_json()["token"]

    resp = client.put(
        "/api/auth/password",
        headers=_auth_header(user_token),
        json={"old_password": "secret123", "new_password": "changed456"},
    )
    assert resp.status_code == 200
    assert client.get(
        "/api/auth/me", headers=_auth_header(resp.get_json()["token"])
    ).status_code == 200
    assert _login(client, "selfchange", "changed456").status_code == 200


def test_change_password_wrong_old_password(client):
    token = _admin_token(client)
    resp = client.put(
        "/api/auth/password",
        headers=_auth_header(token),
        json={"old_password": "wrongpass", "new_password": "brandnew123"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "WRONG_PASSWORD"
    # 原密码不受影响
    assert _login(client, "admin", "123456").status_code == 200


def test_change_password_too_short(client):
    token = _admin_token(client)
    resp = client.put(
        "/api/auth/password",
        headers=_auth_header(token),
        json={"old_password": "123456", "new_password": "123"},
    )
    assert resp.status_code == 400


def test_change_password_requires_auth(client):
    resp = client.put(
        "/api/auth/password",
        json={"old_password": "whatever", "new_password": "brandnew123"},
    )
    assert resp.status_code == 401


def test_list_shows_deactivated_account(client):
    token = _admin_token(client)
    created = client.post(
        "/api/users",
        headers=_auth_header(token),
        json={"username": "victim", "password": "secret123", "role": "employee"},
    )
    victim_id = created.get_json()["id"]
    client.put(f"/api/users/{victim_id}/deactivate", headers=_auth_header(token))

    users = client.get("/api/users", headers=_auth_header(token)).get_json()
    victim = next(user for user in users if user["id"] == victim_id)
    assert victim["is_active"] is False
