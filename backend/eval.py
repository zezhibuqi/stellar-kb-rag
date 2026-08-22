"""RAG 评测脚本（设计文档 10.3）：无权限过滤、全领域 Top-10 → Reranker → Top-5。"""

import argparse
import json
from datetime import date
from pathlib import Path

from langchain_core.documents import Document

import chroma_store
from reranker import SiliconFlowReranker

TARGET_HIT_RATE = 0.8
TARGET_MRR = 0.5


class RAGPipelineForEval:
    """评测专用 RAG 管线：不做权限过滤，全领域检索。"""

    def __init__(self):
        self.reranker = SiliconFlowReranker(top_n=5)

    def retrieve(self, question: str) -> list[Document]:
        candidates = chroma_store.similarity_search(question, k=10)
        reranked = self.reranker.compress_documents(candidates, question)
        return list(reranked)[:5]


def evaluate(golden_set: list[dict], rag_pipeline) -> tuple[dict, dict]:
    """计算总体与分领域的 Hit Rate / MRR。"""
    total = len(golden_set)
    hit = 0
    mrr_sum = 0.0
    per_domain: dict[str, dict] = {}

    for item in golden_set:
        results = rag_pipeline.retrieve(item["question"])
        rank = 0
        found = False
        for rank, doc in enumerate(results, 1):
            if any(keyword in doc.page_content for keyword in item["answer_must_contain"]):
                hit += 1
                mrr_sum += 1 / rank
                found = True
                break
        domain = item.get("domain", "unknown")
        stats = per_domain.setdefault(domain, {"hit": 0, "mrr": 0.0, "total": 0})
        stats["total"] += 1
        if found:
            stats["hit"] += 1
            stats["mrr"] += 1 / rank

    overall = {"hit_rate": hit / total, "mrr": mrr_sum / total}
    domain_stats = {
        domain: {
            "hit_rate": stats["hit"] / stats["total"],
            "mrr": stats["mrr"] / stats["total"],
            "total": stats["total"],
        }
        for domain, stats in sorted(per_domain.items())
    }
    return overall, domain_stats


def generate_report(
    overall: dict,
    domain_stats: dict,
    golden_path: str,
    report_path: str,
) -> None:
    total_questions = sum(stats["total"] for stats in domain_stats.values())
    lines = [
        "# RAG 评测报告",
        "",
        f"- 评测日期：{date.today().isoformat()}",
        "- 数据版本：项目设计文档 V1.8 / 项目开发文档 V1.0",
        f"- Golden Set：{total_questions} 条（{golden_path}）",
        f"- 目标：Hit Rate ≥ {TARGET_HIT_RATE:.0%}，MRR ≥ {TARGET_MRR:.2f}",
        "",
        "## 总体指标",
        "",
        "| 指标 | 数值 | 目标 | 是否达标 |",
        "| --- | --- | --- | --- |",
        (
            f"| Hit Rate | {overall['hit_rate']:.2%} | ≥ {TARGET_HIT_RATE:.0%} | "
            f"{'是' if overall['hit_rate'] >= TARGET_HIT_RATE else '否'} |"
        ),
        (
            f"| MRR | {overall['mrr']:.4f} | ≥ {TARGET_MRR:.2f} | "
            f"{'是' if overall['mrr'] >= TARGET_MRR else '否'} |"
        ),
        "",
        "## 分领域指标",
        "",
        "| 领域 | 条目数 | Hit Rate | MRR |",
        "| --- | --- | --- | --- |",
    ]
    for domain, stats in domain_stats.items():
        lines.append(
            f"| {domain} | {stats['total']} | {stats['hit_rate']:.2%} | {stats['mrr']:.4f} |"
        )

    below_target = [
        domain
        for domain, stats in domain_stats.items()
        if stats["hit_rate"] < TARGET_HIT_RATE or stats["mrr"] < TARGET_MRR
    ]
    lines += ["", "## 未达标领域与改进计划", ""]
    if below_target:
        lines.append("以下领域未达到目标：")
        for domain in below_target:
            stats = domain_stats[domain]
            lines.append(
                f"- {domain}：Hit Rate {stats['hit_rate']:.2%}，MRR {stats['mrr']:.4f}"
            )
        lines += [
            "",
            "改进计划：",
            "1. 表格切分粒度：对接近 chunk_size 边界的表格优化行分组，保证关键参数行完整可检索；",
            "2. 检索召回：评估 k=10 召回不足的领域，必要时将召回提升至 k=20（Reranker 仍取 Top-5）；",
            "3. 查询上下文：对型号类问题保留型号标识，并结合文件名 metadata 做候选二次过滤；",
            "4. Reranker 调优：对比 top_n=5/8 与不同查询表述，选择对表格问答更优的配置；",
            "5. 数据覆盖：补充或细化产品文档，确保参数行与问题表述对应。",
        ]
    else:
        lines.append("所有领域均达到目标。")

    lines += ["", "## 结论", ""]
    if overall["hit_rate"] >= TARGET_HIT_RATE and overall["mrr"] >= TARGET_MRR:
        lines.append("评测指标达到目标，检索与重排管线满足验收要求。")
    else:
        lines.append(
            "评测指标未达目标，需从切片质量、检索 k 值、Reranker 效果、数据覆盖等方面定位并迭代。"
        )

    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 检索质量评测")
    parser.add_argument("--golden", default="docs/golden_set.json")
    parser.add_argument("--report", default="docs/eval_report.md")
    args = parser.parse_args()

    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    overall, domain_stats = evaluate(golden, RAGPipelineForEval())
    generate_report(overall, domain_stats, args.golden, args.report)
    print(
        json.dumps(
            {"overall": overall, "per_domain": domain_stats},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
