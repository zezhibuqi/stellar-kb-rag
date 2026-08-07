"""Stage 5 权限过滤检索测试：各角色的返回领域必须 ⊆ 权限集合。"""

import pytest

import chroma_store
import embeddings
from rag import search_with_permission

EXPECTED_DOMAINS = {
    "employee": {"common", "regulation"},
    "finance": {"common", "finance", "regulation"},
    "sales": {"common", "product", "regulation"},
    "aftersale": {"common", "aftersale", "regulation"},
    "admin": {"common", "finance", "regulation", "product", "aftersale"},
}


def _seed_all_domains(monkeypatch):
    monkeypatch.setattr(
        embeddings,
        "embed_texts",
        lambda texts: [[0.1] * 1024 for _ in texts],
    )
    chroma_store.reset()
    for index, domain in enumerate(
        ["finance", "regulation", "product", "aftersale", "common"]
    ):
        chroma_store.upsert_chunks(
            index + 1,
            domain,
            f"{domain}.md",
            [{"type": "text", "content": f"{domain} 领域内容 {index}"}],
        )


@pytest.mark.parametrize("role", sorted(EXPECTED_DOMAINS))
def test_search_with_permission_filters_domains(role, monkeypatch):
    _seed_all_domains(monkeypatch)
    docs = search_with_permission("领域内容", role, k=10)
    domains = {doc.metadata["domain"] for doc in docs}
    assert domains == EXPECTED_DOMAINS[role]
    assert domains <= EXPECTED_DOMAINS[role]
