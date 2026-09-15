"""模型设置接口测试：权限、切换校验、连通性测试与持久化。"""

import pytest

import llm
from app import create_app


@pytest.fixture()
def client():
    """独立的 Flask 测试客户端。"""
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture()
def scnet_key():
    """临时为 scnet 提供方配置密钥，用例结束后恢复。"""
    original = llm.PROVIDERS["scnet-glm5base"].api_key
    llm.PROVIDERS["scnet-glm5base"].api_key = "sk-test-scnet"
    yield
    llm.PROVIDERS["scnet-glm5base"].api_key = original


@pytest.fixture()
def no_scnet_key():
    """临时清除 scnet 密钥（本机 .env 可能配置了真实密钥），用例结束后恢复。"""
    original = llm.PROVIDERS["scnet-glm5base"].api_key
    llm.PROVIDERS["scnet-glm5base"].api_key = ""
    yield
    llm.PROVIDERS["scnet-glm5base"].api_key = original


def _login(client, username: str, password: str = "123456"):
    """登录并返回 JWT。"""
    resp = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200
    return resp.get_json()["token"]


def _headers(token: str) -> dict:
    """拼装 Bearer 认证头。"""
    return {"Authorization": f"Bearer {token}"}


def test_settings_require_admin(client):
    """模型设置三个接口都只对 admin 开放：非管理员访问返回 403。"""
    token = _login(client, "employee")
    for method, path in (
        (client.get, "/api/settings/model"),
        (client.put, "/api/settings/model"),
        (client.post, "/api/settings/model/test"),
    ):
        resp = method(path, headers=_headers(token), json={})
        assert resp.status_code == 403


def test_get_model_settings_shape(client):
    """设置响应结构固定：active/default + providers 列表，且字段集合精确匹配前端契约。"""
    token = _login(client, "admin")
    resp = client.get("/api/settings/model", headers=_headers(token))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["active"] == "deepseek-v4f"
    assert data["default"] == "deepseek-v4f"
    assert {p["id"] for p in data["providers"]} == {
        "deepseek-v4f",
        "scnet-glm5base",
        "siliconflow-dsv4f",
        "xiaomi-mimov2.5",
        "xiaomi-mimov2.5pro",
    }
    for provider in data["providers"]:
        assert set(provider) == {
            "id",
            "name",
            "platform",
            "base_url",
            "model",
                "api_key_configured",
                "agent_capable",
                "active",
        }


def test_switch_rejects_unknown_provider(client):
    """切换到不存在的提供方返回 404。"""
    token = _login(client, "admin")
    resp = client.put(
        "/api/settings/model",
        headers=_headers(token),
        json={"provider_id": "openai"},
    )
    assert resp.status_code == 404


def test_switch_rejects_unconfigured_key(client, no_scnet_key):
    """密钥未配置的提供方不可切换（400），且不会写入设置。"""
    token = _login(client, "admin")
    resp = client.put(
        "/api/settings/model",
        headers=_headers(token),
        json={"provider_id": "scnet-glm5base"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "PROVIDER_KEY_MISSING"
    # 未写入设置
    resp = client.get("/api/settings/model", headers=_headers(token))
    assert resp.get_json()["active"] == "deepseek-v4f"


def test_switch_persists_and_takes_effect(client, scnet_key):
    """切换成功：响应与再次查询都指向新模型，llm.get_active_provider 同步生效。"""
    token = _login(client, "admin")
    resp = client.put(
        "/api/settings/model",
        headers=_headers(token),
        json={"provider_id": "scnet-glm5base"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["active"] == "scnet-glm5base"
    active_flags = {p["id"]: p["active"] for p in data["providers"]}
    assert active_flags == {
        "deepseek-v4f": False,
        "scnet-glm5base": True,
        "siliconflow-dsv4f": False,
        "xiaomi-mimov2.5": False,
        "xiaomi-mimov2.5pro": False,
    }

    # 再次查询仍为 scnet（DB 持久化）
    resp = client.get("/api/settings/model", headers=_headers(token))
    assert resp.get_json()["active"] == "scnet-glm5base"
    assert llm.get_active_provider().id == "scnet-glm5base"


def test_provider_test_success(client, scnet_key, monkeypatch):
    """连通性测试成功：返回 ok 与模型回显。"""
    monkeypatch.setattr(llm, "test_provider", lambda provider: "pong")
    token = _login(client, "admin")
    resp = client.post(
        "/api/settings/model/test",
        headers=_headers(token),
        json={"provider_id": "scnet-glm5base"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["reply"] == "pong"


def test_provider_test_failure_returns_502(client, monkeypatch):
    """连通性测试失败：返回 502 与 PROVIDER_TEST_FAILED，并把原因透给管理员。"""
    def raise_error(provider):
        """替身：模拟端点连接超时。"""
        raise RuntimeError("连接超时")

    monkeypatch.setattr(llm, "test_provider", raise_error)
    token = _login(client, "admin")
    resp = client.post(
        "/api/settings/model/test",
        headers=_headers(token),
        json={"provider_id": "deepseek-v4f"},
    )
    assert resp.status_code == 502
    assert resp.get_json()["code"] == "PROVIDER_TEST_FAILED"
