"""订单结构化问答评测：真实意图路由 + 真实 SQL，输出答案正确率与路由准确率。"""

import argparse
import json
import re
from datetime import date
from pathlib import Path

import order_qa
import llm
from models import get_connection
from rag import answer_question

TARGET_ANSWER_ACCURACY = 0.85
TARGET_ROUTE_ACCURACY = 0.90


def _norm(text: str) -> str:
    return re.sub(r"[,\s，。、元￥¥]", "", text or "")


def check_answer(expected, mode: str, answer: str) -> bool:
    """按 mode 判定答案是否命中（exact 允许千分位/单位差异归一化）。"""
    if mode == "exact":
        expected_norm = _norm(expected)
        answer_norm = _norm(answer)
        if expected_norm and expected_norm in answer_norm:
            return True
        if expected_norm.endswith(".00"):
            return expected_norm[:-3] in answer_norm
        return False
    return all(keyword in answer for keyword in expected)


def evaluate(golden: list[dict]) -> dict:
    route_hit = 0
    answer_hit = 0
    per_type: dict[str, dict] = {}
    confusion: dict[str, dict] = {}

    for item in golden:
        route = order_qa.route_question(item["question"])
        actual_intent = route["intent"]
        confusion.setdefault(item["intent"], {}).setdefault(actual_intent, 0)
        confusion[item["intent"]][actual_intent] += 1
        if actual_intent == item["intent"]:
            route_hit += 1

        result = answer_question(
            item["question"], history=None, user_role=item["role"], stream=False
        )
        ok = check_answer(item["expected"], item["mode"], result["answer"])
        if ok:
            answer_hit += 1

        item_type = item.get("type", item["intent"])
        stats = per_type.setdefault(item_type, {"total": 0, "hit": 0})
        stats["total"] += 1
        if ok:
            stats["hit"] += 1

    total = len(golden)
    return {
        "answer_accuracy": answer_hit / total,
        "route_accuracy": route_hit / total,
        "per_type": {
            key: {"total": value["total"], "accuracy": value["hit"] / value["total"]}
            for key, value in sorted(per_type.items())
        },
        "confusion": confusion,
    }


def _confusion_value(matrix: dict, expected: str, actual: str) -> int:
    return matrix.get(expected, {}).get(actual, 0)


def generate_report(golden_path: str, results: dict, report_path: str) -> None:
    total_items = sum(value["total"] for value in results["per_type"].values())
    seed_count = get_connection().execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    active = llm.get_active_provider()
    lines = [
        "# 订单结构化问答评测报告",
        "",
        f"- 评测日期：{date.today().isoformat()}",
        f"- 评测模型：{active.name}（{active.model}，{active.platform}）",
        f"- Golden Set：{total_items} 条（{golden_path}）",
        f"- 种子订单数：{seed_count}",
        (
            f"- 目标：答案正确率 ≥ {TARGET_ANSWER_ACCURACY:.0%}，"
            f"路由准确率 ≥ {TARGET_ROUTE_ACCURACY:.0%}"
        ),
        "",
        "## 总体指标",
        "",
        "| 指标 | 数值 | 目标 | 是否达标 |",
        "| --- | --- | --- | --- |",
        (
            f"| 答案正确率 | {results['answer_accuracy']:.2%} | "
            f"≥ {TARGET_ANSWER_ACCURACY:.0%} | "
            f"{'是' if results['answer_accuracy'] >= TARGET_ANSWER_ACCURACY else '否'} |"
        ),
        (
            f"| 路由准确率 | {results['route_accuracy']:.2%} | "
            f"≥ {TARGET_ROUTE_ACCURACY:.0%} | "
            f"{'是' if results['route_accuracy'] >= TARGET_ROUTE_ACCURACY else '否'} |"
        ),
        "",
        "## 分类型答案正确率",
        "",
        "| 类型 | 条目数 | 答案正确率 |",
        "| --- | --- | --- |",
    ]
    for item_type, stats in sorted(results["per_type"].items()):
        lines.append(
            f"| {item_type} | {stats['total']} | {stats['accuracy']:.2%} |"
        )
    lines += [
        "",
        "## 路由混淆矩阵（标注 → 实际）",
        "",
        "| 标注 \\ 实际 | order | knowledge | mixed |",
        "| --- | --- | --- | --- |",
    ]
    for expected in ("order", "knowledge", "mixed"):
        row = [expected]
        for actual in ("order", "knowledge", "mixed"):
            row.append(
                str(_confusion_value(results["confusion"], expected, actual))
            )
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "## 结论", ""]
    if (
        results["answer_accuracy"] >= TARGET_ANSWER_ACCURACY
        and results["route_accuracy"] >= TARGET_ROUTE_ACCURACY
    ):
        lines.append("两项指标均达到目标。")
    else:
        lines.append("未达标，需从路由 few-shot、SQL 模板覆盖或答案生成提示词迭代。")

    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="订单结构化问答评测")
    parser.add_argument("--golden", default="docs/golden_orders.json")
    parser.add_argument("--report", default="docs/eval_orders_report.md")
    args = parser.parse_args()

    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    results = evaluate(golden)
    generate_report(args.golden, results, args.report)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
