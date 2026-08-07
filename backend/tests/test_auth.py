"""Stage 2 认证与用户管理接口测试。"""

import pytest

from app import create_app


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
