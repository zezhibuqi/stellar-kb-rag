"""Stage 5 Reranker 封装测试：降序、metadata 保留、top_n 生效、失败抛错。"""

import pytest
import requests
from langchain_core.documents import Document

from config import Config
from reranker import SiliconFlowReranker


class _FakeResponse:
    """requests.Response 的最小替身：只实现被测代码用到的三个成员。"""

    def __init__(self, status_code: int, data: dict):
        """按入参构造假响应。"""
        self.status_code = status_code
        self._json = data
        self.text = str(data)

    def json(self):
        """返回预置数据（等价于 requests.Response.json()）。"""
        return self._json


def _docs() -> list[Document]:
    """构造 5 条带 doc_id/domain/filename 的文档，便于断言重排后 metadata 未丢。"""
    return [
        Document(
            page_content=f"内容{i}",
            metadata={"doc_id": i, "domain": "finance", "filename": f"f{i}.md"},
        )
        for i in range(5)
    ]


def test_reranker_orders_by_score_and_keeps_metadata(monkeypatch):
    """按 relevance_score 降序取 top_n，且文档内容与 metadata 原样保留。"""
    def fake_post(url, json=None, headers=None, timeout=None):
        """伪造 rerank 接口：故意返回乱序结果，验证客户端自己排序。"""
        assert json["model"] == "BAAI/bge-reranker-v2-m3"
        assert json["top_n"] == 3
        return _FakeResponse(
            200,
            {
                "results": [
                    {"index": 3, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.3},
                    {"index": 1, "relevance_score": 0.7},
                    {"index": 2, "relevance_score": 0.5},
                    {"index": 4, "relevance_score": 0.1},
                ]
            },
        )

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(Config, "SILICONFLOW_API_KEY", "test-key")
    result = SiliconFlowReranker(top_n=3).compress_documents(_docs(), "查询")
    assert [doc.metadata["doc_id"] for doc in result] == [3, 1, 2]
    assert result[0].metadata["filename"] == "f3.md"
    assert result[0].page_content == "内容3"


def test_reranker_failure_raises(monkeypatch):
    """非 200 一律抛 RerankerError（含状态码），由上层转 500，不做静默降级。"""
    def bad_post(*args, **kwargs):
        """伪造 500 响应。"""
        return _FakeResponse(500, {})

    monkeypatch.setattr(requests, "post", bad_post)
    monkeypatch.setattr(Config, "SILICONFLOW_API_KEY", "test-key")
    with pytest.raises(RuntimeError, match="500"):
        SiliconFlowReranker().compress_documents(_docs(), "查询")


def test_reranker_empty_documents():
    """空输入直接返回空列表，不发起网络请求（也避免 API 报参数错误）。"""
    assert SiliconFlowReranker().compress_documents([], "查询") == []


def test_reranker_requires_api_key(monkeypatch):
    """密钥未配置时给出明确错误，而不是发出必然 401 的请求。"""
    monkeypatch.setattr(Config, "SILICONFLOW_API_KEY", "")
    with pytest.raises(RuntimeError, match="SILICONFLOW_API_KEY"):
        SiliconFlowReranker().compress_documents(_docs(), "查询")
