"""Stage 7 评测逻辑测试：Golden Set 数据完整性与 Hit Rate/MRR 计算。"""

import json
from collections import Counter
from pathlib import Path

from langchain_core.documents import Document

import rag
from eval import RAGPipelineForEval, evaluate


def test_golden_set_coverage():
    """Golden Set 数据完整性：≥75 条、五个领域各 ≥15 条、每条三要素齐全。"""
    golden = json.loads(
        Path("docs/golden_set.json").read_text(encoding="utf-8")
    )
    assert len(golden) >= 75
    per_domain = Counter(item["domain"] for item in golden)
    assert set(per_domain) == {"finance", "common", "product", "aftersale", "regulation"}
    assert all(count >= 15 for count in per_domain.values())
    for item in golden:
        assert item["question"]
        assert item["answer_must_contain"]
        assert item["source_file"]

    sc100_density = next(
        item
        for item in golden
        if item["domain"] == "product"
        and item["question"] == "SC-100电池的单体能量密度（质量）是多少？"
    )
    assert sc100_density["answer_must_contain"] == ["180 Wh/kg"]


def _doc(content: str) -> Document:
    """构造评测用的文档对象（domain 固定 finance，只用于命中判定）。"""
    return Document(page_content=content, metadata={"domain": "finance"})


class FakePipeline:
    """用「问题 → 命中列表」映射替身真实检索管线，让指标计算可被精确断言。"""

    def __init__(self, mapping: dict):
        """保存预置的 问题→文档列表 映射。"""
        self.mapping = mapping

    def retrieve(self, question: str):
        """按问题返回预置结果（等价于 RAGPipelineForEval.retrieve 的输出）。"""
        return self.mapping[question]


def test_evaluate_hit_rate_and_mrr():
    """Hit Rate 与 MRR 的计算口径：命中位置越靠前 MRR 越高，未命中不计入 MRR 分子。"""
    golden = [
        {
            "domain": "finance",
            "question": "q1",
            "answer_must_contain": ["A"],
            "source_file": "a.md",
        },
        {
            "domain": "finance",
            "question": "q2",
            "answer_must_contain": ["B"],
            "source_file": "b.md",
        },
    ]
    pipeline = FakePipeline(
        {
            "q1": [_doc("命中 A"), _doc("无关")],
            "q2": [_doc("无关"), _doc("无关2"), _doc("命中 B")],
        }
    )
    overall, domain_stats = evaluate(golden, pipeline)
    assert overall == {"hit_rate": 1.0, "mrr": (1 + 1 / 3) / 2}
    assert domain_stats["finance"]["total"] == 2
    assert domain_stats["finance"]["hit_rate"] == 1.0


def test_eval_pipeline_uses_standard_hybrid_search(monkeypatch):
    """评测管线复用标准模式的混合检索入口，而不是另走纯向量路径。"""
    captured: dict = {}

    def fake_search(question: str, user_role: str):
        """替身：记录评测调用查询的角色与问题。"""
        captured["question"] = question
        captured["user_role"] = user_role
        return [
            {
                "text": "证据单元原文",
                "doc_id": 7,
                "chunk_id": 2,
                "filename": "f.md",
                "domain": "product",
                "start_line": 12,
                "parent_start_line": 10,
                "parent_end_line": 20,
            }
        ]

    monkeypatch.setattr(rag, "hybrid_search_with_permission", fake_search)
    docs = RAGPipelineForEval().retrieve("产品问题")
    assert captured == {"question": "产品问题", "user_role": "admin"}
    assert docs[0].page_content == "证据单元原文"
    assert docs[0].metadata["doc_id"] == 7
