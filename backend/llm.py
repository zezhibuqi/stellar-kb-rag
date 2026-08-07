"""DeepSeek LLM 客户端（OpenAI 兼容接口）。"""

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


def invoke(prompt: str) -> str:
    response = get_client().chat.completions.create(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": prompt}],
        temperature=Config.LLM_TEMPERATURE,
        max_tokens=Config.LLM_MAX_TOKENS,
    )
    return response.choices[0].message.content or ""


def stream(prompt: str):
    response = get_client().chat.completions.create(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": prompt}],
        temperature=Config.LLM_TEMPERATURE,
        max_tokens=Config.LLM_MAX_TOKENS,
        stream=True,
    )
    for chunk in response:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
