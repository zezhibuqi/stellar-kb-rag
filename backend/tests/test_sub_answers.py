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
