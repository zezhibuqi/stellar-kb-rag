"""模型设置接口测试：权限、切换校验、连通性测试与持久化。"""

import pytest

import llm
from app import create_app


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture()
def scnet_key():
    """临时为 scnet 提供方配置密钥，用例结束后恢复。"""
    original = llm.PROVIDERS["scnet"].api_key
    llm.PROVIDERS["scnet"].api_key = "sk-test-scnet"
    yield
    llm.PROVIDERS["scnet"].api_key = original


def _login(client, username: str, password: str = "123456"):
    resp = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200
    return resp.get_json()["token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_settings_require_admin(client):
    token = _login(client, "employee")
    for method, path in (
        (client.get, "/api/settings/model"),
        (client.put, "/api/settings/model"),
        (client.post, "/api/settings/model/test"),
    ):
        resp = method(path, headers=_headers(token), json={})
        assert resp.status_code == 403


def test_get_model_settings_shape(client):
    token = _login(client, "admin")
    resp = client.get("/api/settings/model", headers=_headers(token))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["active"] == "deepseek"
    assert data["default"] == "deepseek"
    assert {p["id"] for p in data["providers"]} == {"deepseek", "scnet"}
    for provider in data["providers"]:
        assert set(provider) == {
            "id",
            "name",
            "platform",
            "base_url",
            "model",
            "api_key_configured",
            "active",
        }


def test_switch_rejects_unknown_provider(client):
    token = _login(client, "admin")
    resp = client.put(
        "/api/settings/model",
        headers=_headers(token),
        json={"provider_id": "openai"},
    )
    assert resp.status_code == 404


def test_switch_rejects_unconfigured_key(client):
    # 测试环境未配置 SCNET_API_KEY，切换应被拒绝
    assert not llm.PROVIDERS["scnet"].api_key
    token = _login(client, "admin")
    resp = client.put(
        "/api/settings/model",
        headers=_headers(token),
        json={"provider_id": "scnet"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "PROVIDER_KEY_MISSING"
    # 未写入设置
    resp = client.get("/api/settings/model", headers=_headers(token))
    assert resp.get_json()["active"] == "deepseek"


def test_switch_persists_and_takes_effect(client, scnet_key):
    token = _login(client, "admin")
    resp = client.put(
        "/api/settings/model",
        headers=_headers(token),
        json={"provider_id": "scnet"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["active"] == "scnet"
    active_flags = {p["id"]: p["active"] for p in data["providers"]}
    assert active_flags == {"deepseek": False, "scnet": True}

    # 再次查询仍为 scnet（DB 持久化）
    resp = client.get("/api/settings/model", headers=_headers(token))
    assert resp.get_json()["active"] == "scnet"
    assert llm.get_active_provider().id == "scnet"


def test_switch_back_to_deepseek(client, scnet_key):
    token = _login(client, "admin")
    client.put("/api/settings/model", headers=_headers(token), json={"provider_id": "scnet"})
    resp = client.put(
        "/api/settings/model", headers=_headers(token), json={"provider_id": "deepseek"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["active"] == "deepseek"


def test_provider_test_success(client, scnet_key, monkeypatch):
    monkeypatch.setattr(llm, "test_provider", lambda provider: "pong")
    token = _login(client, "admin")
    resp = client.post(
        "/api/settings/model/test",
        headers=_headers(token),
        json={"provider_id": "scnet"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["reply"] == "pong"


def test_provider_test_failure_returns_502(client, monkeypatch):
    def raise_error(provider):
        raise RuntimeError("连接超时")

    monkeypatch.setattr(llm, "test_provider", raise_error)
    token = _login(client, "admin")
    resp = client.post(
        "/api/settings/model/test",
        headers=_headers(token),
        json={"provider_id": "deepseek"},
    )
    assert resp.status_code == 502
    assert resp.get_json()["code"] == "PROVIDER_TEST_FAILED"
