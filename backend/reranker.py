"""SiliconFlow Reranker 封装（设计文档 3.4）：继承 BaseDocumentCompressor。"""

from typing import Sequence

import requests
from langchain.retrievers.document_compressors.base import BaseDocumentCompressor
from langchain_core.documents import Document

from config import Config


class RerankerError(RuntimeError):
    """Reranker 调用失败（不降级，由上层返回 HTTP 500）。"""


class SiliconFlowReranker(BaseDocumentCompressor):
    """调用 SiliconFlow /v1/rerank 对检索结果重排；失败时抛异常（不降级）。"""

    top_n: int = 5

    def compress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks=None,
    ) -> Sequence[Document]:
        if not documents:
            return []
        if not Config.SILICONFLOW_API_KEY:
            raise RerankerError("SILICONFLOW_API_KEY 未配置")

        url = Config.SILICONFLOW_BASE_URL.rstrip("/") + "/rerank"
        payload = {
            "model": "BAAI/bge-reranker-v2-m3",
            "query": query,
            "documents": [doc.page_content for doc in documents],
            "top_n": self.top_n,
        }
        headers = {"Authorization": f"Bearer {Config.SILICONFLOW_API_KEY}"}
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=60)
        except Exception as exc:  # noqa: BLE001 - 网络异常统一归为重排失败
            raise RerankerError(f"Reranker API 调用失败: {exc}") from exc
        if resp.status_code != 200:
            raise RerankerError(
                f"Reranker API 返回 {resp.status_code}: {resp.text[:200]}"
            )

        results = sorted(
            resp.json()["results"],
            key=lambda item: item["relevance_score"],
            reverse=True,
        )
        return [documents[item["index"]] for item in results[: self.top_n]]
