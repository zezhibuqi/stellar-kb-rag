"""Stage 5 Reranker 封装测试：降序、metadata 保留、top_n 生效、失败抛错。"""

import pytest
import requests
from langchain_core.documents import Document

from config import Config
from reranker import SiliconFlowReranker


class _FakeResponse:
    def __init__(self, status_code: int, data: dict):
        self.status_code = status_code
        self._json = data
        self.text = str(data)

    def json(self):
        return self._json


def _docs() -> list[Document]:
    return [
        Document(
            page_content=f"内容{i}",
            metadata={"doc_id": i, "domain": "finance", "filename": f"f{i}.md"},
        )
        for i in range(5)
    ]


def test_reranker_orders_by_score_and_keeps_metadata(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
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
    def bad_post(*args, **kwargs):
        return _FakeResponse(500, {})

    monkeypatch.setattr(requests, "post", bad_post)
    monkeypatch.setattr(Config, "SILICONFLOW_API_KEY", "test-key")
    with pytest.raises(RuntimeError, match="500"):
        SiliconFlowReranker().compress_documents(_docs(), "查询")


def test_reranker_empty_documents():
    assert SiliconFlowReranker().compress_documents([], "查询") == []


def test_reranker_requires_api_key(monkeypatch):
    monkeypatch.setattr(Config, "SILICONFLOW_API_KEY", "")
    with pytest.raises(RuntimeError, match="SILICONFLOW_API_KEY"):
        SiliconFlowReranker().compress_documents(_docs(), "查询")
