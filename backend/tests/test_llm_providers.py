"""模型提供方注册表与当前模型解析测试。"""

from types import SimpleNamespace

import pytest

import llm
from config import Config
from models import get_setting, set_setting


@pytest.fixture()
def scnet_key(monkeypatch):
    """临时为 scnet 提供方配置密钥，用例结束后恢复。"""
    original = llm.PROVIDERS["scnet"].api_key
    llm.PROVIDERS["scnet"].api_key = "sk-test-scnet"
    yield
    llm.PROVIDERS["scnet"].api_key = original


@pytest.fixture()
def no_scnet_key():
    """临时清除 scnet 密钥（本机 .env 可能配置了真实密钥），用例结束后恢复。"""
    original = llm.PROVIDERS["scnet"].api_key
    llm.PROVIDERS["scnet"].api_key = ""
    yield
    llm.PROVIDERS["scnet"].api_key = original


def _fake_client(calls: list, stream: bool = False, reply: str = "ok"):
    def create(**kwargs):
        calls.append(kwargs)
        if stream:
            chunk = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="token"), finish_reason=None
                    )
                ]
            )
            return iter([chunk])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))]
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_registry_contains_preset_providers():
    providers = {p.id: p for p in llm.list_providers()}
    assert set(providers) == {"deepseek", "scnet"}
    assert providers["deepseek"].model == "deepseek-v4-flash"
    assert providers["deepseek"].base_url == Config.DEEPSEEK_BASE_URL
    assert providers["scnet"].model == Config.SCNET_MODEL
    assert providers["scnet"].base_url == "https://api.scnet.cn/api/llm/v1"


def test_active_provider_defaults_to_env(monkeypatch):
    monkeypatch.setattr(Config, "LLM_PROVIDER", "deepseek")
    assert llm.get_active_provider().id == "deepseek"


def test_active_provider_follows_db_setting(scnet_key):
    assert get_setting(llm.SETTING_KEY) is None
    set_setting(llm.SETTING_KEY, "scnet")
    assert llm.get_active_provider().id == "scnet"


def test_active_provider_falls_back_on_invalid_setting(monkeypatch):
    monkeypatch.setattr(Config, "LLM_PROVIDER", "deepseek")
    set_setting(llm.SETTING_KEY, "not-a-provider")
    assert llm.get_active_provider().id == "deepseek"


def test_get_client_rejects_missing_key(no_scnet_key):
    set_setting(llm.SETTING_KEY, "scnet")
    with pytest.raises(RuntimeError, match="API Key 未配置"):
        llm.get_client()


def test_invoke_uses_active_provider_model(monkeypatch, scnet_key):
    calls: list = []
    monkeypatch.setattr(llm, "get_client", lambda provider=None: _fake_client(calls))
    monkeypatch.setattr(Config, "LLM_PROVIDER", "deepseek")
    assert llm.invoke("hi") == "ok"
    assert calls[0]["model"] == "deepseek-v4-flash"

    set_setting(llm.SETTING_KEY, "scnet")
    assert llm.invoke("hi") == "ok"
    assert calls[1]["model"] == Config.SCNET_MODEL


def test_invoke_json_uses_active_provider_model(monkeypatch, scnet_key):
    calls: list = []
    fake = _fake_client(calls, reply='{"intent": "knowledge"}')
    monkeypatch.setattr(llm, "get_client", lambda provider=None: fake)
    set_setting(llm.SETTING_KEY, "scnet")
    result = llm.invoke_json('输出 {"intent": "knowledge"}')
    assert result == {"intent": "knowledge"}
    assert calls[0]["model"] == Config.SCNET_MODEL
    # scnet（GLM-5-Base）不支持 response_format，应跳过且加大路由 token 预算
    assert "response_format" not in calls[0]
    assert calls[0]["max_tokens"] == 2000


def test_invoke_json_deepseek_keeps_response_format(monkeypatch):
    calls: list = []
    fake = _fake_client(calls, reply='{"intent": "knowledge"}')
    monkeypatch.setattr(llm, "get_client", lambda provider=None: fake)
    result = llm.invoke_json('输出 {"intent": "knowledge"}')
    assert result == {"intent": "knowledge"}
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["max_tokens"] == 300


def test_invoke_empty_content_raises(monkeypatch):
    fake = _fake_client([], reply="")
    monkeypatch.setattr(llm, "get_client", lambda provider=None: fake)
    with pytest.raises(RuntimeError, match="未返回内容"):
        llm.invoke("hi")


def test_stream_truncated_without_content_raises(monkeypatch):
    calls: list = []

    def create(**kwargs):
        calls.append(kwargs)
        chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=None), finish_reason="length"
                )
            ]
        )
        return iter([chunk])

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm, "get_client", lambda provider=None: fake)
    with pytest.raises(RuntimeError, match="截断"):
        list(llm.stream("hi"))


def test_stream_uses_active_provider_model(monkeypatch, scnet_key):
    calls: list = []
    monkeypatch.setattr(llm, "get_client", lambda provider=None: _fake_client(calls, stream=True))
    set_setting(llm.SETTING_KEY, "scnet")
    tokens = list(llm.stream("hi"))
    assert tokens == ["token"]
    assert calls[0]["model"] == Config.SCNET_MODEL
    assert calls[0]["stream"] is True
