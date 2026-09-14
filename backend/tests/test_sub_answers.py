"""子答案步测试（设计文档 7.8）：证据配额与去重、并行作答、失败隔离。"""

import orchestrator
from config import Config


def _knowledge_item(doc_id: int, start: int, end: int, name: str = "f.md") -> dict:
    return {
        "doc_id": doc_id,
        "filename": name,
        "domain": "finance",
        "chunk_type": "table",
        "start_line": start,
        "parent_type": "table",
        "parent_start_line": start,
        "parent_end_line": end,
        "text": f"内容 {doc_id}-{start}",
    }


def _sub(qid: int, query: str, source: str = "knowledge", **extra) -> dict:
    return {
        "id": qid,
        "query": query,
        "source": source,
        "filters": extra.get("filters", {}),
        "aggregation": extra.get("aggregation"),
    }


def _search(items):
    return lambda query: {"status": "ok", "items": list(items)}


def _search_by_query(mapping):
    return lambda query: {"status": "ok", "items": list(mapping.get(query, []))}


def test_evidence_deduped_across_sub_questions():
    items = [
        _knowledge_item(index, index * 10, index * 10 + 5, f"{index}.md")
        for index in range(1, 4)
    ]
    evidence = orchestrator.collect_evidence(
        [_sub(1, "a"), _sub(2, "b"), _sub(3, "c")], _search(items), lambda *a: {}
    )
    assigned = [item for entry in evidence.values() for item in entry["items"]]
    keys = {
        (item["doc_id"], item["parent_start_line"], item["parent_end_line"])
        for item in assigned
    }
    assert len(keys) == len(assigned), "同一证据单元不得重复分配"
    assert len(evidence[1]["items"]) == Config.AGENT_EVIDENCE_PER_SUB
    assert evidence[3]["items"] == [], "证据被前面占满后不再分配"


def test_evidence_respects_limits():
    items = [_knowledge_item(index, index * 10, index * 10 + 5) for index in range(1, 8)]
    sub_questions = [_sub(index, f"q{index}") for index in range(1, 7)]
    evidence = orchestrator.collect_evidence(sub_questions, _search(items), lambda *a: {})
    total = sum(len(entry["items"]) for entry in evidence.values())
    assert total <= Config.AGENT_EVIDENCE_GLOBAL
    assert all(
        len(entry["items"]) <= Config.AGENT_EVIDENCE_PER_SUB
        for entry in evidence.values()
    )


def test_answer_sub_questions_runs_and_normalizes(monkeypatch):
    prompts: list[str] = []

    def fake_invoke_json(prompt, **kwargs):
        prompts.append(prompt)
        return {
            "answer": "1688 万元",
            "coverage": "sufficient",
            "evidence_ids": [1],
            "key_entities": ["SC-300", "extra", "third"],
        }

    monkeypatch.setattr(orchestrator.llm, "invoke_json", fake_invoke_json)
    results = orchestrator.answer_sub_questions(
        {"sub_questions": [_sub(1, "a"), _sub(2, "b")]},
        "问题",
        _search_by_query({"a": [_knowledge_item(1, 1, 2)], "b": [_knowledge_item(2, 3, 4)]}),
        lambda *a: {},
    )
    assert len(prompts) == 2
    assert all(item["coverage"] == "sufficient" for item in results)
    assert all(item["error"] is None for item in results)
    assert results[0]["key_entities"] == ["SC-300", "extra"], "关键实体最多保留两个"


def test_answer_sub_questions_isolates_failure(monkeypatch):
    def fake_invoke_json(prompt, **kwargs):
        if "本次要回答的子问题：b" in prompt:
            raise RuntimeError("上游失败")
        return {"answer": "OK", "coverage": "sufficient"}

    monkeypatch.setattr(orchestrator.llm, "invoke_json", fake_invoke_json)
    results = orchestrator.answer_sub_questions(
        {"sub_questions": [_sub(1, "a"), _sub(2, "b")]},
        "问题",
        _search_by_query({"a": [_knowledge_item(1, 1, 2)], "b": [_knowledge_item(2, 3, 4)]}),
        lambda *a: {},
    )
    failed = [item for item in results if item["error"]]
    assert len(failed) == 1 and failed[0]["id"] == 2
    assert results[0]["answer"] == "OK"


def test_order_sub_question_denied_is_data_not_error():
    plan = {"sub_questions": [_sub(1, "订单", source="order", filters={"order_no": "X"})]}
    results = orchestrator.answer_sub_questions(
        plan,
        "问题",
        _search([]),
        lambda filters, aggregation: {
            "status": "denied",
            "rows": [],
            "aggregation": None,
            "truncated": False,
        },
    )
    assert results[0]["source"] == "order"
    assert results[0]["order"]["status"] == "denied"
    assert results[0]["coverage"] == "missing"
    assert results[0]["error"] is None


def test_no_sub_questions_returns_empty():
    assert (
        orchestrator.answer_sub_questions({}, "问题", _search([]), lambda *a: {}) == []
    )


def test_sub_answer_uses_configured_token_budget(monkeypatch):
    captured = {}

    def fake_invoke_json(prompt, **kwargs):
        captured.update(kwargs)
        return {"answer": "A", "coverage": "sufficient"}

    monkeypatch.setattr(orchestrator.llm, "invoke_json", fake_invoke_json)
    orchestrator.answer_sub_questions(
        {"sub_questions": [_sub(1, "a")]},
        "问题",
        _search([_knowledge_item(1, 1, 2)]),
        lambda *a: {},
    )
    assert captured["max_tokens"] == Config.AGENT_SUB_ANSWER_MAX_TOKENS


def _round_one_result(key_entities):
    return {
        "id": 1,
        "query": "SC-500 配套 电芯 型号",
        "source": "knowledge",
        "answer": "SC-300",
        "coverage": "sufficient",
        "evidence_ids": [],
        "key_entities": key_entities,
        "evidence": [],
        "order": None,
        "error": None,
    }


def _pending():
    return [
        {
            "id": 2,
            "query": "{1} 单体质量能量密度 25℃ 循环寿命",
            "source": "knowledge",
            "filters": {},
            "aggregation": None,
            "depends_on": 1,
        }
    ]


def test_round_two_fills_placeholder_from_key_entities():
    plan = orchestrator.build_round_two_plan(
        [_round_one_result(["SC-300"])], [], pending=_pending(), limit=8
    )
    assert plan["sub_questions"][0]["query"] == "SC-300 单体质量能量密度 25℃ 循环寿命"
    assert plan["sub_questions"][0]["follow_up_of"] == 1
    assert plan["unresolved_pending"] == []


def test_round_two_uses_intent_terms_when_entity_missing():
    """拿不到中间实体时，用该子问题去掉占位符的意图词检索（不是整条原问题）。"""
    pending = _pending()
    plan = orchestrator.build_round_two_plan(
        [_round_one_result([])], [], pending=pending, limit=8
    )
    assert plan["unresolved_pending"] == []
    assert (
        plan["sub_questions"][0]["query"]
        == "单体质量能量密度 25℃ 循环寿命"
    )


def test_round_two_fallback_keeps_original_question_as_alt_query():
    """意图词作主查询，原问题保留为备用查询（双查询交替取用）。"""
    plan = orchestrator.build_round_two_plan(
        [_round_one_result([])],
        [],
        pending=_pending(),
        limit=8,
        question="财务总监在年报里是以什么身份作出声明的？",
    )
    assert plan["unresolved_pending"] == []
    assert plan["sub_questions"][0]["query"] == "单体质量能量密度 25℃ 循环寿命"
    assert (
        plan["sub_questions"][0]["alt_query"]
        == "财务总监在年报里是以什么身份作出声明的？"
    )


def test_round_two_marks_alt_query_for_gap_fill():
    plan = orchestrator.build_round_two_plan(
        [_round_one_result(["SC-300"])], [1], limit=8, question="原问题"
    )
    assert plan["sub_questions"][0]["alt_query"] == "原问题"


def test_round_two_adds_stripped_query_variant():
    plan = orchestrator.build_round_two_plan(
        [_round_one_result(["SC-300"])],
        [],
        pending=_pending(),
        limit=8,
        question="原问题",
    )
    assert plan["sub_questions"][0]["stripped_query"] == "单体质量能量密度 25℃ 循环寿命"


def test_collect_evidence_searches_all_query_variants():
    searched: list[str] = []

    def search(query):
        searched.append(query)
        index = len(searched)
        return {
            "status": "ok",
            "items": [_knowledge_item(index, index * 10, index * 10 + 2)],
        }

    sub_question = _sub(1, "实体式查询")
    sub_question["alt_query"] = "原问题"
    sub_question["stripped_query"] = "意图词"
    evidence = orchestrator.collect_evidence([sub_question], search, lambda *a: {})

    assert searched == ["实体式查询", "原问题", "意图词"]
    assert len(evidence[1]["items"]) == Config.AGENT_EVIDENCE_PER_SUB


def test_collect_evidence_merges_alt_query_without_duplicates():
    primary = _knowledge_item(1, 10, 20, "a.md")
    alternative = _knowledge_item(2, 30, 40, "b.md")

    def search(query):
        return {"status": "ok", "items": [alternative if query == "原问题" else primary]}

    sub_question = _sub(1, "实体式查询")
    sub_question["alt_query"] = "原问题"
    evidence = orchestrator.collect_evidence([sub_question], search, lambda *a: {})
    docs = [item["doc_id"] for item in evidence[1]["items"]]
    assert sorted(docs) == [1, 2], "两个查询的命中应合并"
    assert len(docs) == len(set(docs)), "合并后不得重复"


def test_round_two_skips_entity_query_already_covered_by_pending():
    """链式第二跳已经用该实体查过时，缺口补充不再用同一个实体重复检索。"""
    result = _round_one_result(["SC-300"])
    result["coverage"] = "partial"
    plan = orchestrator.build_round_two_plan(
        [result], [1], pending=_pending(), limit=8
    )
    assert len(plan["sub_questions"]) == 1
    assert plan["sub_questions"][0]["query"].startswith("SC-300 单体质量能量密度")


def test_round_two_keeps_distinct_entity_queries():
    result = _round_one_result(["SC-300", "SC-400"])
    plan = orchestrator.build_round_two_plan([result], [1], pending=None, limit=8)
    assert [item["query"] for item in plan["sub_questions"]] == ["SC-300", "SC-400"]
