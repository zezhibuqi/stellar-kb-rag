"""模型提供方注册表与当前模型解析测试。"""

from types import SimpleNamespace

import pytest

import llm
from config import Config
from models import get_setting, set_setting


@pytest.fixture()
def xiaomi_key():
    """临时为小米 flash 提供方配置密钥，用例结束后恢复。"""
    provider = llm.PROVIDERS["xiaomi-mimov2.6-flash"]
    original = provider.api_key
    provider.api_key = "sk-test-xiaomi"
    yield
    provider.api_key = original


@pytest.fixture()
def no_xiaomi_key():
    """临时清除小米 flash 密钥（本机 .env 可能配置了真实密钥），用例结束后恢复。"""
    provider = llm.PROVIDERS["xiaomi-mimov2.6-flash"]
    original = provider.api_key
    provider.api_key = ""
    yield
    provider.api_key = original


@pytest.fixture()
def thinking_provider():
    """向注册表注入一个「非默认能力」提供方，覆盖 capability 分支。

    scnet 下线后，注册表里已没有关闭 response_format / stream_usage 的真实端点；
    用注入的替身保住这些分支的测试覆盖，且不依赖任何外部平台。
    """
    provider = llm.ModelProvider(
        id="test-thinking",
        name="Test-Thinking",
        platform="测试",
        base_url="https://example.invalid/v1",
        model="test-thinking",
        api_key="sk-test-thinking",
        router_max_tokens=2000,
        supports_response_format=False,
        supports_stream_usage=False,
    )
    llm.PROVIDERS[provider.id] = provider
    yield provider
    llm.PROVIDERS.pop(provider.id, None)
    llm._clients.pop(provider.id, None)


def _fake_client(calls: list, stream: bool = False, reply: str = "ok"):
    """构造假的 OpenAI 客户端：记录每次请求参数，并按需返回流式/非流式响应。

    假响应故意不带 usage 字段，用来覆盖「端点不返回用量」的真实情况。
    """
    def create(**kwargs):
        """记录请求参数（calls）并返回预置的响应对象。"""
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
    """注册表内置 4 个提供方；scnet 已按 ADR 0011 下线，小米为 v2.6 两条。"""
    providers = {p.id: p for p in llm.list_providers()}
    assert set(providers) == {
        "deepseek-flash",
        "siliconflow-dsv4f",
        "xiaomi-mimov2.6-flash",
        "xiaomi-mimov2.6-pro",
    }
    assert "scnet-glm5base" not in providers
    assert providers["deepseek-flash"].model == "deepseek-flash"
    assert providers["deepseek-flash"].base_url == Config.DEEPSEEK_BASE_URL
    assert providers["xiaomi-mimov2.6-flash"].model == "mimo-v2.6-flash"
    assert providers["xiaomi-mimov2.6-pro"].model == "mimo-v2.6-pro"
    assert providers["xiaomi-mimov2.6-flash"].agent_capable is False


def test_active_provider_defaults_to_env(monkeypatch):
    """DB 无设置时，当前模型取 .env 的 LLM_PROVIDER。"""
    monkeypatch.setattr(Config, "LLM_PROVIDER", "deepseek-flash")
    assert llm.get_active_provider().id == "deepseek-flash"



def test_active_provider_follows_db_setting(xiaomi_key):
    """DB 有设置时以 DB 为准（管理员界面切换后重启仍然生效）。"""
    assert get_setting(llm.SETTING_KEY) is None
    set_setting(llm.SETTING_KEY, "xiaomi-mimov2.6-flash")
    assert llm.get_active_provider().id == "xiaomi-mimov2.6-flash"


def test_active_provider_falls_back_on_invalid_setting(monkeypatch):
    """DB 里的提供方被移除（失效 id）时自动回退默认提供方，不抛异常。"""
    monkeypatch.setattr(Config, "LLM_PROVIDER", "deepseek-flash")
    set_setting(llm.SETTING_KEY, "not-a-provider")
    assert llm.get_active_provider().id == "deepseek-flash"


def test_get_client_rejects_missing_key(no_xiaomi_key):
    """密钥未配置的提供方取客户端即报错，避免发出必然失败的请求。"""
    set_setting(llm.SETTING_KEY, "xiaomi-mimov2.6-flash")
    with pytest.raises(RuntimeError, match="API Key 未配置"):
        llm.get_client()


def test_invoke_uses_active_provider_model(monkeypatch, xiaomi_key):
    """invoke 用当前模型发请求；切换后下一次调用即换模型（无需重启）。"""
    calls: list = []
    monkeypatch.setattr(llm, "get_client", lambda provider=None: _fake_client(calls))
    monkeypatch.setattr(Config, "LLM_PROVIDER", "deepseek-flash")
    assert llm.invoke("hi") == "ok"
    assert calls[0]["model"] == "deepseek-flash"

    set_setting(llm.SETTING_KEY, "xiaomi-mimov2.6-flash")
    assert llm.invoke("hi") == "ok"
    assert calls[1]["model"] == "mimo-v2.6-flash"


def test_invoke_json_uses_active_provider_model(monkeypatch, thinking_provider):
    """JSON 调用跟随当前模型；关闭 response_format 的端点走「跳过 + 加大预算」分支。"""
    calls: list = []
    fake = _fake_client(calls, reply='{"intent": "knowledge"}')
    monkeypatch.setattr(llm, "get_client", lambda provider=None: fake)
    set_setting(llm.SETTING_KEY, thinking_provider.id)
    result = llm.invoke_json('输出 {"intent": "knowledge"}')
    assert result == {"intent": "knowledge"}
    assert calls[0]["model"] == "test-thinking"
    # 该端点不支持 response_format，应跳过且加大路由 token 预算
    assert "response_format" not in calls[0]
    assert calls[0]["max_tokens"] == 2000


def test_invoke_json_deepseek_keeps_response_format(monkeypatch):
    """支持 response_format 的端点保留 json_object，并使用提供方的路由预算 300。"""
    calls: list = []
    fake = _fake_client(calls, reply='{"intent": "knowledge"}')
    monkeypatch.setattr(llm, "get_client", lambda provider=None: fake)
    result = llm.invoke_json('输出 {"intent": "knowledge"}')
    assert result == {"intent": "knowledge"}
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["max_tokens"] == 300


def test_invoke_empty_content_raises(monkeypatch):
    """模型返回空内容时显式报错（多为思考型模型推理耗尽预算），不返回空回答。"""
    fake = _fake_client([], reply="")
    monkeypatch.setattr(llm, "get_client", lambda provider=None: fake)
    with pytest.raises(RuntimeError, match="未返回内容"):
        llm.invoke("hi")


def test_stream_truncated_without_content_raises(monkeypatch):
    """流式全程无 token 且 finish_reason=length：报错提示调大 LLM_MAX_TOKENS。"""
    calls: list = []

    def create(**kwargs):
        """返回一个只有 finish_reason、没有内容的流式分片。"""
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


def test_stream_uses_active_provider_model(monkeypatch, xiaomi_key):
    """流式调用走当前模型，并且请求里 stream=True。"""
    calls: list = []
    monkeypatch.setattr(llm, "get_client", lambda provider=None: _fake_client(calls, stream=True))
    set_setting(llm.SETTING_KEY, "xiaomi-mimov2.6-flash")
    tokens = list(llm.stream("hi"))
    assert tokens == ["token"]
    assert calls[0]["model"] == "mimo-v2.6-flash"
    assert calls[0]["stream"] is True


# ── token 用量采集（trace 用，设计文档 7.8）──────────────────────────────


def _usage_client(calls: list, reply: str = '{"a": 1}', usage=None):
    """构造带 usage 的假客户端，用于验证 token 用量采集。"""
    def create(**kwargs):
        """记录请求参数并返回带 usage 的响应。"""
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
            usage=usage,
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_invoke_json_records_token_usage(monkeypatch):
    """非流式 JSON 调用把 usage 写进调用方传入的字典（trace 用）。"""
    calls: list = []
    usage = SimpleNamespace(prompt_tokens=11, completion_tokens=2, total_tokens=13)
    monkeypatch.setattr(
        llm, "get_client", lambda provider=None: _usage_client(calls, usage=usage)
    )
    sink: dict = {}
    assert llm.invoke_json('输出 {"a": 1}', usage=sink) == {"a": 1}
    assert sink == {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13}


def test_invoke_json_without_usage_sink_still_works(monkeypatch):
    """不传 usage 出口时行为不变（老调用方无感，向后兼容）。"""
    calls: list = []
    monkeypatch.setattr(llm, "get_client", lambda provider=None: _usage_client(calls))
    assert llm.invoke_json('输出 {"a": 1}') == {"a": 1}


def test_stream_requests_and_records_usage(monkeypatch):
    """流式请求带 stream_options.include_usage，并记录最后一个分片里的用量。"""
    calls: list = []

    def create(**kwargs):
        """返回「内容分片 + 仅含 usage 的收尾分片」，模拟 OpenAI 的流式用量约定。"""
        calls.append(kwargs)
        chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="token"), finish_reason=None
                )
            ],
            usage=None,
        )
        final = SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=1, total_tokens=6),
        )
        return iter([chunk, final])

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm, "get_client", lambda provider=None: fake)
    sink: dict = {}
    assert list(llm.stream("hi", usage=sink)) == ["token"]
    assert calls[0]["stream_options"] == {"include_usage": True}
    assert sink == {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}


def test_stream_retries_without_stream_options_when_unsupported(monkeypatch):
    """端点不认 stream_options 时退回普通流式：用量留空，但回答照常产出。"""
    calls: list = []

    def create(**kwargs):
        """带 stream_options 时抛 TypeError（模拟端点不支持），否则正常返回分片。"""
        calls.append(kwargs)
        if "stream_options" in kwargs:
            raise TypeError("unexpected keyword argument 'stream_options'")
        chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="token"), finish_reason=None
                )
            ],
            usage=None,
        )
        return iter([chunk])

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm, "get_client", lambda provider=None: fake)
    sink: dict = {}
    assert list(llm.stream("hi", usage=sink)) == ["token"]
    assert len(calls) == 2
    assert "stream_options" not in calls[1]
    assert sink == {}


def test_injected_provider_disables_stream_usage(monkeypatch, thinking_provider):
    """非默认能力标志必须被调用层尊重：关闭用量时不带 stream_options。"""
    calls: list = []
    monkeypatch.setattr(
        llm, "get_client", lambda provider=None: _fake_client(calls, stream=True)
    )
    set_setting(llm.SETTING_KEY, thinking_provider.id)
    sink: dict = {}
    assert list(llm.stream("hi", usage=sink)) == ["token"]
    assert "stream_options" not in calls[0]
    assert sink == {}


def test_health_reports_stored_and_active_provider():
    """健康检查同时暴露 DB 存储值与实际生效模型；失效 id 时 fallback=True。"""
    from app import create_app

    client = create_app().test_client()
    set_setting(llm.SETTING_KEY, "not-a-provider")
    data = client.get("/api/health").get_json()["llm_provider"]
    assert data["stored"] == "not-a-provider"
    assert data["active"] == llm.DEFAULT_PROVIDER_ID
    assert data["fallback"] is True

    set_setting(llm.SETTING_KEY, llm.DEFAULT_PROVIDER_ID)
    data = client.get("/api/health").get_json()["llm_provider"]
    assert data["fallback"] is False
