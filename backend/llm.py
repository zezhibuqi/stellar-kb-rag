"""DeepSeek LLM 客户端（OpenAI 兼容接口）。"""

import json
import re

from openai import OpenAI

from config import Config

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not Config.DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY 未配置")
        _client = OpenAI(
            api_key=Config.DEEPSEEK_API_KEY,
            base_url=Config.DEEPSEEK_BASE_URL,
        )
    return _client


def invoke(prompt: str, temperature: float | None = None) -> str:
    if temperature is None:
        temperature = Config.LLM_TEMPERATURE
    response = get_client().chat.completions.create(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=Config.LLM_MAX_TOKENS,
    )
    return response.choices[0].message.content or ""


def invoke_json(prompt: str, temperature: float = 0.0, max_tokens: int = 300) -> dict:
    """JSON 输出调用封装（意图路由用）；兼容不支持 response_format 的端点。"""
    common = {
        "model": Config.ROUTER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        response = get_client().chat.completions.create(
            response_format={"type": "json_object"}, **common
        )
    except Exception:  # noqa: BLE001 - 兼容端点不支持 response_format
        response = get_client().chat.completions.create(**common)
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
    response = get_client().chat.completions.create(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=Config.LLM_MAX_TOKENS,
        stream=True,
    )
    for chunk in response:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
