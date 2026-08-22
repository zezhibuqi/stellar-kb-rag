"""LLM 客户端（OpenAI 兼容接口）：预设提供方注册表 + 管理员可切换的当前模型。

当前模型的解析顺序：app_settings 表的 llm_provider 值 → .env 的 LLM_PROVIDER
→ 注册表中的 deepseek 兜底。回答生成与意图路由共用同一当前模型。
"""

import json
import re
from dataclasses import dataclass

from openai import OpenAI

from config import Config
from models import get_setting

SETTING_KEY = "llm_provider"


@dataclass
class ModelProvider:
    """预设模型提供方：密钥只来自 .env，不入库、不回显。"""

    id: str
    name: str
    platform: str
    base_url: str
    model: str
    api_key: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


def _build_providers() -> dict[str, ModelProvider]:
    providers = [
        ModelProvider(
            id="deepseek",
            name="DeepSeek",
            platform="DeepSeek 开放平台",
            base_url=Config.DEEPSEEK_BASE_URL,
            model="deepseek-v4-flash",
            api_key=Config.DEEPSEEK_API_KEY,
        ),
        ModelProvider(
            id="scnet",
            name="GLM-5-Base",
            platform="国家超算互联网（scnet）",
            base_url=Config.SCNET_BASE_URL,
            model=Config.SCNET_MODEL,
            api_key=Config.SCNET_API_KEY,
        ),
    ]
    return {provider.id: provider for provider in providers}


PROVIDERS = _build_providers()
DEFAULT_PROVIDER_ID = (
    Config.LLM_PROVIDER if Config.LLM_PROVIDER in PROVIDERS else "deepseek"
)

_clients: dict[str, OpenAI] = {}


def list_providers() -> list[ModelProvider]:
    return list(PROVIDERS.values())


def get_provider(provider_id: str) -> ModelProvider | None:
    return PROVIDERS.get(provider_id)


def get_active_provider() -> ModelProvider:
    """解析当前模型；DB 值缺失或已失效时回退默认提供方。"""
    stored = get_setting(SETTING_KEY) or ""
    return PROVIDERS.get(stored) or PROVIDERS[DEFAULT_PROVIDER_ID]


def get_client(provider: ModelProvider | None = None) -> OpenAI:
    if provider is None:
        provider = get_active_provider()
    if not provider.api_key:
        raise RuntimeError(f"{provider.name} 的 API Key 未配置")
    client = _clients.get(provider.id)
    if client is None:
        client = OpenAI(api_key=provider.api_key, base_url=provider.base_url)
        _clients[provider.id] = client
    return client


def test_provider(provider: ModelProvider) -> str:
    """连通性测试：发送一次最小调用，失败抛异常。"""
    response = get_client(provider).chat.completions.create(
        model=provider.model,
        messages=[{"role": "user", "content": "ping"}],
        temperature=0.0,
        max_tokens=8,
    )
    return response.choices[0].message.content or ""


def invoke(prompt: str, temperature: float | None = None) -> str:
    if temperature is None:
        temperature = Config.LLM_TEMPERATURE
    provider = get_active_provider()
    response = get_client(provider).chat.completions.create(
        model=provider.model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=Config.LLM_MAX_TOKENS,
    )
    return response.choices[0].message.content or ""


def invoke_json(prompt: str, temperature: float = 0.0, max_tokens: int = 300) -> dict:
    """JSON 输出调用封装（意图路由用）；兼容不支持 response_format 的端点。"""
    provider = get_active_provider()
    common = {
        "model": provider.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        response = get_client(provider).chat.completions.create(
            response_format={"type": "json_object"}, **common
        )
    except Exception:  # noqa: BLE001 - 兼容端点不支持 response_format
        response = get_client(provider).chat.completions.create(**common)
    content = response.choices[0].message.content or ""
    return _parse_json_text(content)


def _parse_json_text(text: str) -> dict:
    """宽容解析：支持 markdown 代码块包裹或前后多余文本。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def stream(prompt: str, temperature: float | None = None):
    if temperature is None:
        temperature = Config.LLM_TEMPERATURE
    provider = get_active_provider()
    response = get_client(provider).chat.completions.create(
        model=provider.model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=Config.LLM_MAX_TOKENS,
        stream=True,
    )
    for chunk in response:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
