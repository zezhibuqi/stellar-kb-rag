"""合成步测试（设计文档 7.8）：逐项落位、缺项显式、数值以证据为准。"""

import orchestrator


def _result(qid, coverage="sufficient", answer="答案", evidence=None, error=None):
    return {
        "id": qid,
        "query": f"子问题{qid}",
        "source": "knowledge",
        "answer": answer,
        "coverage": coverage,
        "evidence_ids": [1] if evidence else [],
        "key_entities": [],
        "evidence": evidence or [],
        "order": None,
        "error": error,
    }


def _evidence():
    return [
        {
            "doc_id": 1,
            "filename": "2025年度报告.md",
            "domain": "finance",
            "parent_start_line": 10,
            "parent_end_line": 12,
            "text": "| 项目 | 值 |\n|---|---|\n| 净利润 | 1688 |",
        }
    ]


def test_prompt_contains_each_sub_question_with_evidence():
    prompt = orchestrator.build_synthesis_prompt(
        "2025 年经营情况如何？",
        [_result(1, evidence=_evidence()), _result(2, coverage="missing", answer="")],
    )
    assert "【子问题 1】子问题1" in prompt
    assert "证据充分" in prompt
    assert "证据缺失" in prompt
    assert "1688" in prompt
    assert orchestrator.NO_EVIDENCE_NOTE in prompt
    assert "用户问题：2025 年经营情况如何？" in prompt


def test_prompt_reports_failed_sub_question_state():
    prompt = orchestrator.build_synthesis_prompt(
        "问题", [_result(1, error="上游失败", answer="")]
    )
    assert "作答失败（上游失败）" in prompt


def test_unresolved_sub_questions_lists_partial_missing_and_failed():
    results = [
        _result(1),
        _result(2, coverage="partial"),
        _result(3, coverage="missing"),
        _result(4, error="超时"),
    ]
    assert orchestrator.unresolved_sub_questions(results) == [2, 3, 4]


def test_synthesize_uses_llm_and_returns_text(monkeypatch):
    captured = {}

    def fake_invoke(prompt, **kwargs):
        captured["prompt"] = prompt
        return "最终回答"

    monkeypatch.setattr(orchestrator.llm, "invoke", fake_invoke)
    answer = orchestrator.synthesize("问题", [_result(1, evidence=_evidence())])
    assert answer == "最终回答"
    assert "子问题 1" in captured["prompt"]
    assert "1688" in captured["prompt"]


def test_order_result_is_rendered_as_evidence():
    item = _result(1)
    item["source"] = "order"
    item["order"] = {
        "status": "ok",
        "rows": [],
        "aggregation": {"type": "count", "value": 11},
        "truncated": False,
    }
    prompt = orchestrator.build_synthesis_prompt("未完成订单有几笔？", [item])
    assert "订单数量 = 11" in prompt
