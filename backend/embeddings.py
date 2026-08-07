"""SiliconFlow Embedding 客户端：批量请求 + 重试 + 超长文本截断。"""

import logging
import time

import requests

from config import Config

logger = logging.getLogger("embeddings")

MAX_INPUT_CHARS = 8192  # bge-m3 最大输入 8192 token，按字符数粗略截断
BATCH_SIZE = 32
RETRY_TIMES = 3
RETRY_BACKOFF_SECONDS = 1.0


def embed_texts(texts: list[str]) -> list[list[float]]:
    """返回与输入顺序一致的 1024 维向量列表。"""
    if not Config.SILICONFLOW_API_KEY:
        raise RuntimeError("SILICONFLOW_API_KEY 未配置")

    truncated = [text[:MAX_INPUT_CHARS] for text in texts]
    vectors: list[list[float]] = []
    for start in range(0, len(truncated), BATCH_SIZE):
        vectors.extend(_embed_batch(truncated[start : start + BATCH_SIZE]))
    return vectors


def _embed_batch(texts: list[str]) -> list[list[float]]:
    url = Config.SILICONFLOW_BASE_URL.rstrip("/") + "/embeddings"
    payload = {"model": "BAAI/bge-m3", "input": texts}
    headers = {"Authorization": f"Bearer {Config.SILICONFLOW_API_KEY}"}

    last_exc: Exception | None = None
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=60)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Embedding API 返回 {resp.status_code}: {resp.text[:200]}"
                )
            data = resp.json()
            items = sorted(data["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in items]
            if any(len(v) != 1024 for v in vectors):
                logger.warning("Embedding 返回维度异常：%s", [len(v) for v in vectors])
            return vectors
        except Exception as exc:  # noqa: BLE001 - 网络/响应异常均需重试
            last_exc = exc
            if attempt < RETRY_TIMES:
                logger.warning("Embedding 第 %s/%s 次失败：%s", attempt, RETRY_TIMES, exc)
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    raise RuntimeError(f"Embedding 请求重试 {RETRY_TIMES} 次仍失败: {last_exc}")
