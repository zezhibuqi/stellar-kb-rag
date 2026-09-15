"""多模型对比评测：订单答案正确率、意图路由准确率、检索对照指标与流式延迟。

对注册表中每个已配置密钥的提供方依次执行：
- 订单 Golden Set：路由准确率 + 答案正确率（复用 eval_orders 逻辑）；
- 检索 Golden Set：Hit Rate / MRR 全程不含 LLM 调用，仅执行一次，
  各模型数值应一致，作为对照组；
- 固定提示词流式调用若干次，统计平均首 token 延迟与平均总延迟。

运行前提：知识库已完成灌库（检索对照需要向量数据）。
注意：评测会临时切换 app_settings 的当前模型，脚本结束时恢复原值；
中断后重跑会自动跳过已完成的提供方（断点文件 --data）。
"""

import argparse
import json
import time
from datetime import date
from pathlib import Path

import llm
from eval import RAGPipelineForEval, evaluate as evaluate_retrieval
from eval_orders import evaluate as evaluate_orders
from models import get_setting, set_setting

DEFAULT_LATENCY_RUNS = 3

LATENCY_PROMPT = (
    "你是企业知识助手。请根据参考资料用不超过三句话回答问题。\n\n"
    "参考资料：\n星辰科技集团的核心价值观是：诚信为本、创新驱动、客户至上、绿色共赢。\n\n"
    "用户：公司的核心价值观是什么？"
)


def _mean(values: list[float]) -> float | None:
    """算术平均并保留 3 位小数；无样本返回 None（报告里显示为「评测失败」）。"""
    return round(sum(values) / len(values), 3) if values else None


def measure_stream_latency(stream_fn, runs: int, prompt: str = LATENCY_PROMPT) -> dict:
    """对 stream_fn(prompt) 逐 token 计时；返回平均首 token/总延迟（秒）。"""
    ttfts: list[float] = []
    totals: list[float] = []
    failed = 0
    for _ in range(runs):
        start = time.perf_counter()
        first_at = None
        try:
            for _token in stream_fn(prompt):
                if first_at is None:
                    first_at = time.perf_counter()
        except Exception:  # noqa: BLE001 - 单次失败计入 failed，不中断整体评测
            pass
        end = time.perf_counter()
        if first_at is None:
            failed += 1
        else:
            ttfts.append(first_at - start)
            totals.append(end - start)
    return {
        "ttft": _mean(ttfts),
        "total": _mean(totals),
        "runs_ok": len(ttfts),
        "runs_failed": failed,
    }


def evaluate_provider(provider_id: str, golden_orders: list[dict], latency_runs: int) -> dict:
    """切换当前模型后执行订单评测与延迟测量。"""
    set_setting(llm.SETTING_KEY, provider_id)
    result = {"provider_id": provider_id}
    try:
        order_result = evaluate_orders(golden_orders)
        result["answer_accuracy"] = order_result["answer_accuracy"]
        result["route_accuracy"] = order_result["route_accuracy"]
    except Exception as exc:  # noqa: BLE001 - 单个模型失败不阻塞其余模型
        result["error"] = str(exc)
    result["latency"] = measure_stream_latency(llm.stream, latency_runs)
    return result


def _pct(value: float | None) -> str:
    """百分比单元格格式；None 表示该模型没跑出结果。"""
    return f"{value:.2%}" if value is not None else "评测失败"


def _sec(value: float | None) -> str:
    """秒数单元格格式；None 表示没有有效样本。"""
    return f"{value:.2f}s" if value is not None else "-"


def build_report(
    results: list[dict],
    providers_by_id: dict,
    retrieval_overall: dict | None,
    latency_runs: int,
    golden_orders_path: str,
    golden_retrieval_path: str,
) -> str:
    """渲染多模型对比表：订单正确率/路由准确率/检索对照/首 token 与总延迟。

    检索指标与所用 LLM 无关（管线里没有 LLM），表里标注为对照组；
    未配置密钥的提供方单列一节，避免读者误以为它们评测失败。
    """
    hit_rate_cell = (
        f"{retrieval_overall['hit_rate']:.2%}" if retrieval_overall else "未执行"
    )
    mrr_cell = f"{retrieval_overall['mrr']:.4f}" if retrieval_overall else "-"
    lines = [
        "# 多模型对比评测报告",
        "",
        f"- 评测日期：{date.today().isoformat()}",
        f"- 订单 Golden Set：{golden_orders_path}（答案正确率 / 路由准确率）",
        f"- 检索 Golden Set：{golden_retrieval_path}（对照组，管线不含 LLM）",
        f"- 延迟样本：每模型 {latency_runs} 次固定提示词流式调用取平均",
        "",
        "| 模型 | 平台 | 订单答案正确率 | 路由准确率 | 检索 Hit Rate* | 检索 MRR* | 平均首 token 延迟 | 平均总延迟 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    skipped: list[str] = []
    errors: list[str] = []
    for item in results:
        provider = providers_by_id[item["provider_id"]]
        if not provider.configured:
            skipped.append(provider.name)
            continue
        latency = item.get("latency") or {}
        lines.append(
            f"| {provider.model} | {provider.platform} "
            f"| {_pct(item.get('answer_accuracy'))} | {_pct(item.get('route_accuracy'))} "
            f"| {hit_rate_cell} | {mrr_cell} "
            f"| {_sec(latency.get('ttft'))} | {_sec(latency.get('total'))} |"
        )
        if item.get("error"):
            errors.append(f"{provider.name}：{item['error']}")
        if latency.get("runs_failed"):
            errors.append(
                f"{provider.name}：{latency['runs_failed']} 次延迟采样未产生任何 token"
            )
    lines += [
        "",
        "\\* 检索指标由向量召回 + 重排产生，与所用 LLM 无关，各模型应完全一致，仅作对照。",
        "",
    ]
    if skipped:
        lines += ["## 未参与评测（密钥未配置）", ""]
        lines += [f"- {name}" for name in skipped]
        lines.append("")
    if errors:
        lines += ["## 异常记录", ""]
        lines += [f"- {message}" for message in errors]
        lines.append("")
    return "\n".join(lines)


def _load_checkpoint(path: str) -> dict[str, dict]:
    """读取断点文件（provider_id → 结果）；缺失或损坏时返回空，等价于从头跑。"""
    data_path = Path(path)
    if not data_path.exists():
        return {}
    try:
        items = json.loads(data_path.read_text(encoding="utf-8"))
        return {item["provider_id"]: item for item in items}
    except (json.JSONDecodeError, KeyError):
        return {}


def _save_checkpoint(path: str, results: list[dict]) -> None:
    """写断点：每评完一个提供方就落盘，中断后重跑可跳过已完成的模型。"""
    data_path = Path(path)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    """命令行入口：逐提供方评订单问答与延迟，最后补一次检索对照并写报告。

    会临时改写 app_settings.llm_provider，结束时在 finally 中恢复原值；
    未配置密钥的提供方只登记、不评测。
    """
    parser = argparse.ArgumentParser(description="多模型对比评测")
    parser.add_argument("--golden-orders", default="docs/golden_orders.json")
    parser.add_argument("--golden-retrieval", default="docs/golden_set.json")
    parser.add_argument("--report", default="docs/model_comparison_report.md")
    parser.add_argument("--data", default="docs/model_comparison_data.json")
    parser.add_argument("--latency-runs", type=int, default=DEFAULT_LATENCY_RUNS)
    parser.add_argument(
        "--fresh", action="store_true", help="忽略断点数据，全部重新评测"
    )
    args = parser.parse_args()

    golden_orders = json.loads(Path(args.golden_orders).read_text(encoding="utf-8"))
    golden_retrieval = json.loads(
        Path(args.golden_retrieval).read_text(encoding="utf-8")
    )
    providers = llm.list_providers()
    providers_by_id = {provider.id: provider for provider in providers}

    done = {} if args.fresh else _load_checkpoint(args.data)

    original = get_setting(llm.SETTING_KEY)

    def restore():
        """恢复评测前的当前模型设置（被打断也要保证设置不残留）。"""
        set_setting(llm.SETTING_KEY, original or llm.DEFAULT_PROVIDER_ID)

    try:
        results: list[dict] = []
        pending = [
            provider
            for provider in providers
            if provider.id not in done or not provider.configured
        ]
        for index, provider in enumerate(pending, start=1):
            print(
                f"[{index}/{len(pending)}] {provider.id} ({provider.name}) 评测中...",
                flush=True,
            )
            if not provider.configured:
                results.append({"provider_id": provider.id})
                continue
            results.append(
                evaluate_provider(provider.id, golden_orders, args.latency_runs)
            )
            _save_checkpoint(
                args.data, [done[p.id] for p in providers if p.id in done] + results
            )

        final_results = []
        by_id = {item["provider_id"]: item for item in results}
        for provider in providers:
            final_results.append(by_id.get(provider.id) or done[provider.id])

        retrieval_overall = None
        chroma_ready = _chroma_count() > 0
        if chroma_ready:
            print("检索对照评测中（与模型无关，仅一次）...", flush=True)
            overall, _domains = evaluate_retrieval(
                golden_retrieval, RAGPipelineForEval()
            )
            retrieval_overall = overall
        else:
            print("警告：Chroma 为空，跳过检索对照评测（请先灌库）", flush=True)

        report = build_report(
            final_results,
            providers_by_id,
            retrieval_overall,
            args.latency_runs,
            args.golden_orders,
            args.golden_retrieval,
        )
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report + "\n", encoding="utf-8")
        print(report)
    finally:
        restore()


def _chroma_count() -> int:
    """Chroma 向量总数；为 0 时跳过检索对照（未灌库环境下评测无意义）。"""
    import chroma_store

    return int(chroma_store.get_collection().count())


if __name__ == "__main__":
    main()
