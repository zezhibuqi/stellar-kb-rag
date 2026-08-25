"""多模型对比评测逻辑测试：延迟统计与报告渲染（离线，不调用外部服务）。"""

import time
from types import SimpleNamespace

from eval_models import build_report, measure_stream_latency


def _provider(pid, name="N", platform="P", model="m", configured=True):
    return SimpleNamespace(
        id=pid, name=name, platform=platform, model=model, configured=configured
    )


def test_measure_stream_latency_averages():
    def fake_stream(_prompt):
        time.sleep(0.01)
        yield "tok1"
        yield "tok2"

    stats = measure_stream_latency(fake_stream, runs=2)
    assert stats["runs_ok"] == 2
    assert stats["runs_failed"] == 0
    assert 0 < stats["ttft"] <= stats["total"]


def test_measure_stream_latency_counts_failures():
    def broken(_prompt):
        raise RuntimeError("连接超时")
        yield  # noqa: unreachable - 使其成为惰性生成器

    stats = measure_stream_latency(broken, runs=2)
    assert stats["ttft"] is None
    assert stats["total"] is None
    assert stats["runs_failed"] == 2


def test_build_report_renders_table_and_skipped_list():
    providers = {
        "a": _provider("a", name="ModelA", platform="平台A", model="model-a"),
        "b": _provider("b", name="ModelB", platform="平台B", model="model-b",
                       configured=False),
    }
    results = [
        {
            "provider_id": "a",
            "answer_accuracy": 0.9524,
            "route_accuracy": 1.0,
            "latency": {"ttft": 0.5, "total": 2.25, "runs_ok": 3, "runs_failed": 0},
        },
        {"provider_id": "b"},
    ]
    text = build_report(
        results,
        providers,
        {"hit_rate": 0.88, "mrr": 0.8322},
        latency_runs=3,
        golden_orders_path="docs/golden_orders.json",
        golden_retrieval_path="docs/golden_set.json",
    )
    assert (
        "| model-a | 平台A | 95.24% | 100.00% | 88.00% | 0.8322 | 0.50s | 2.25s |"
        in text
    )
    assert "- ModelB" in text
    assert "\\* 检索指标" in text


def test_build_report_without_retrieval_marks_not_executed():
    providers = {"a": _provider("a", model="model-a")}
    results = [
        {
            "provider_id": "a",
            "answer_accuracy": 0.9,
            "route_accuracy": 0.9,
            "latency": {"ttft": 1.0, "total": 3.0, "runs_ok": 1, "runs_failed": 0},
        }
    ]
    text = build_report(
        results,
        providers,
        None,
        latency_runs=1,
        golden_orders_path="g.json",
        golden_retrieval_path="r.json",
    )
    assert "未执行" in text
