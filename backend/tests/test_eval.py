"""Stage 7 评测逻辑测试：Hit Rate/MRR 计算与报告生成。"""

import json
from collections import Counter
from pathlib import Path

from langchain_core.documents import Document

from eval import RAGPipelineForEval, evaluate, generate_report


def test_golden_set_coverage():
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
    return Document(page_content=content, metadata={"domain": "finance"})


class FakePipeline:
    def __init__(self, mapping: dict):
        self.mapping = mapping

    def retrieve(self, question: str):
        return self.mapping[question]


def test_evaluate_hit_rate_and_mrr():
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


def test_generate_report(tmp_path):
    overall = {"hit_rate": 0.9, "mrr": 0.6}
    domain_stats = {
        "finance": {"hit_rate": 0.9, "mrr": 0.6, "total": 10},
        "common": {"hit_rate": 0.8, "mrr": 0.5, "total": 15},
    }
    report = tmp_path / "eval_report.md"
    generate_report(overall, domain_stats, "docs/golden_set.json", str(report))
    text = report.read_text(encoding="utf-8")
    assert "Hit Rate" in text
    assert "90.00%" in text
    assert "| common | 15 | 80.00% | 0.5000 |" in text
