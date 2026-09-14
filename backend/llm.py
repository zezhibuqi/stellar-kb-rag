"""LLM 客户端（OpenAI 兼容接口）：预设提供方注册表 + 管理员可切换的当前模型。

当前模型的解析顺序：app_settings 表的 llm_provider 值 → .env 的 LLM_PROVIDER
→ 注册表中的 deepseek-v4f 兜底。回答生成与意图路由共用同一当前模型。
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
    # 思考型模型（如 GLM-5-Base）先输出 reasoning_content 再输出 content：
    # JSON 路由需要预留推理 token 预算
    router_max_tokens: int = 300
    # scnet GLM-5-Base 上 response_format=json_object 会返回乱序文本（finish=abort），
    # 不支持的能力需在注册表中显式关闭
    supports_response_format: bool = True
    # 增强模式（编排式问答）需要可靠的结构化输出与低延迟；不具备该标志的提供方
    # 在增强模式下不可选中，系统不会为了跑通而在背后换模型（ADR 0008）
    agent_capable: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


def _build_providers() -> dict[str, ModelProvider]:
    providers = [
        ModelProvider(
            id="deepseek-v4f",
            name="DeepSeek-V4-Flash-0731",
            platform="DeepSeek 开放平台",
            base_url=Config.DEEPSEEK_BASE_URL,
            model="deepseek-v4-flash",
            api_key=Config.DEEPSEEK_API_KEY,
            agent_capable=True,
        ),
        ModelProvider(
            id="scnet-glm5base",
            name="GLM-5-Base",
            platform="国家超算互联网（scnet）",
            base_url=Config.SCNET_BASE_URL,
            model="GLM-5-Base",
            api_key=Config.SCNET_API_KEY,
            router_max_tokens=2000,
            supports_response_format=False,
        ),
        ModelProvider(
            id="siliconflow-dsv4f",
            name="DeepSeek-V4-Flash-0731",
            platform="硅基流动（SiliconFlow）",
            base_url=Config.SILICONFLOW_BASE_URL,
            model="deepseek-ai/DeepSeek-V4-Flash",
            api_key=Config.SILICONFLOW_API_KEY,
            agent_capable=True,
        ),
        ModelProvider(
            id="xiaomi-mimov2.5",
            name="MIMO-V2.5",
            platform="小米",
            base_url=Config.XIAOMI_BASE_URL,
            model="mimo-v2.5",
            api_key=Config.XIAOMI_API_KEY,
            router_max_tokens=2000,
        ),
        ModelProvider(
            id="xiaomi-mimov2.5pro",
            name="MIMO-V2.5-Pro",
            platform="小米",
            base_url=Config.XIAOMI_BASE_URL,
            model="mimo-v2.5-pro",
            api_key=Config.XIAOMI_API_KEY,
            router_max_tokens=2000,
        ),
        # ── 新增模型提供方示例（两处配套：本注册表 + config.py/.env 的密钥与地址）──
        # 以硅基流动（SiliconFlow）平台的 deepseek-ai/DeepSeek-V4-Flash 为例，
        # 复用已有的 SILICONFLOW_API_KEY（Embedding/Reranker 同平台）。
        # 模型标识直接写在 model 字段（与上方 deepseek 条目一致），不经过环境变量；
        # 同平台可注册多个模型条目，id 唯一即可，base_url 与 api_key 可复用。
        #
        # ModelProvider(
        #     id="siliconflow",                          # 唯一标识（英文小写）
        #     name="DeepSeek-V4-Flash",                  # /settings 卡片显示名
        #     platform="硅基流动（SiliconFlow）",          # /settings 卡片平台名
        #     base_url=Config.SILICONFLOW_LLM_BASE_URL,
        #     model="deepseek-ai/DeepSeek-V4-Flash",     # 平台的模型标识
        #     api_key=Config.SILICONFLOW_LLM_KEY,
        #     # 能力标志（普通对话模型用默认值即可）：
        #     # 思考型模型（先输出 reasoning_content 再输出 content，如 GLM-5-Base）
        #     # 需预留路由推理预算：router_max_tokens=2000；
        #     # 传 response_format=json_object 会报错/乱码的端点：supports_response_format=False
        #     # router_max_tokens=2000,
        #     # supports_response_format=False,
        # ),
    ]
    return {provider.id: provider for provider in providers}


PROVIDERS = _build_providers()
DEFAULT_PROVIDER_ID = (
    Config.LLM_PROVIDER if Config.LLM_PROVIDER in PROVIDERS else "deepseek-v4f"
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
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("模型未返回内容（推理可能耗尽 max_tokens，请调大 LLM_MAX_TOKENS）")
    return content


def invoke_json(prompt: str, temperature: float = 0.0, max_tokens: int | None = None) -> dict:
    """JSON 输出调用封装（意图路由用）；兼容不支持 response_format 的提供方。"""
    provider = get_active_provider()
    if max_tokens is None:
        max_tokens = provider.router_max_tokens
    common = {
        "model": provider.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if provider.supports_response_format:
        try:
            response = get_client(provider).chat.completions.create(
                response_format={"type": "json_object"}, **common
            )
        except Exception:  # noqa: BLE001 - 兼容端点不支持 response_format
            response = get_client(provider).chat.completions.create(**common)
    else:
        response = get_client(provider).chat.completions.create(**common)
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("模型未返回 JSON 内容（推理可能耗尽 max_tokens）")
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
    emitted = False
    finish_reason = None
    for chunk in response:
        if chunk.choices:
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            if choice.delta and choice.delta.content:
                emitted = True
                yield choice.delta.content
    if not emitted and finish_reason == "length":
        raise RuntimeError("模型输出被 max_tokens 截断，未生成回答内容")
