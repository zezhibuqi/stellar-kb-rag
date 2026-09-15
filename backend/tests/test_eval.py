"""Stage 7 评测逻辑测试：Golden Set 数据完整性与 Hit Rate/MRR 计算。"""

import json
from collections import Counter
from pathlib import Path

from langchain_core.documents import Document

from eval import evaluate


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
