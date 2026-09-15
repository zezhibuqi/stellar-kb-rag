"""子答案步测试（设计文档 7.8）：证据配额与去重、并行作答、失败隔离。"""

import orchestrator
from config import Config


def _knowledge_item(doc_id: int, start: int, end: int, name: str = "f.md") -> dict:
    """构造一个知识证据候选（带证据单元行区间与原文），用于拼装检索结果。"""
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
    """构造一个子问题（结构对齐编排器内部契约）。"""
    return {
        "id": qid,
        "query": query,
        "source": source,
        "filters": extra.get("filters", {}),
        "aggregation": extra.get("aggregation"),
    }


def _search(items):
    """返回「任何查询都命中同一批 items」的检索替身。"""
    return lambda query: {"status": "ok", "items": list(items)}


def _search_by_query(mapping):
    """返回按查询串取结果的检索替身（用于区分不同查询变体）。"""
    return lambda query: {"status": "ok", "items": list(mapping.get(query, []))}


def test_evidence_deduped_across_sub_questions():
    """跨子问题的证据单元不重复分配：先到先得，后面的子问题拿到剩余证据。"""
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
    """双重配额生效：每子问题不超过 per_sub，总量不超过全局上限。"""
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
    """并行作答：每个子问题一次调用，结果归一化（关键实体最多保留 2 个）。"""
    prompts: list[str] = []

    def fake_invoke_json(prompt, **kwargs):
        """替身：记录提示词并返回固定的子答案 JSON。"""
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
    """失败隔离：一个子问题抛错不影响其它子问题，错误只记录在该子问题上。"""
    def fake_invoke_json(prompt, **kwargs):
        """按提示词内容区分：子问题 b 抛错，其余正常作答。"""
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
    """订单越权在子答案层是「数据」而非异常：order.status=denied、error 为空。"""
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
    """空计划直接返回空列表（不启动线程池，也不产生 LLM 调用）。"""
    assert (
        orchestrator.answer_sub_questions({}, "问题", _search([]), lambda *a: {}) == []
    )


def test_sub_answer_uses_configured_token_budget(monkeypatch):
    """子答案调用使用 AGENT_SUB_ANSWER_MAX_TOKENS，而不是提供方的默认路由预算。"""
    captured = {}

    def fake_invoke_json(prompt, **kwargs):
        """记录 kwargs 以便断言 max_tokens。"""
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


def test_sub_answer_records_usage_and_elapsed(monkeypatch):
    """trace 需要的每步耗时与 token 用量：子答案步逐个记录（设计文档 7.8）。"""

    def fake_invoke_json(prompt, **kwargs):
        """替身：顺手往 usage 出口写一份用量，模拟端点返回 usage。"""
        sink = kwargs.get("usage")
        if sink is not None:
            sink.update({"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})
        return {"answer": "A", "coverage": "sufficient"}

    monkeypatch.setattr(orchestrator.llm, "invoke_json", fake_invoke_json)
    results = orchestrator.answer_sub_questions(
        {"sub_questions": [_sub(1, "a")]},
        "问题",
        _search([_knowledge_item(1, 1, 2)]),
        lambda *a: {},
    )
    assert results[0]["usage"]["total_tokens"] == 10
    assert results[0]["elapsed_ms"] is not None
    assert results[0]["elapsed_ms"] >= 0


def test_failed_sub_answer_still_records_elapsed(monkeypatch):
    """作答失败也要留下耗时，便于区分「模型慢」与「模型报错」。"""
    def boom(*args, **kwargs):
        """替身：直接抛错。"""
        raise RuntimeError("上游失败")

    monkeypatch.setattr(orchestrator.llm, "invoke_json", boom)
    results = orchestrator.answer_sub_questions(
        {"sub_questions": [_sub(1, "a")]},
        "问题",
        _search([_knowledge_item(1, 1, 2)]),
        lambda *a: {},
    )
    assert results[0]["error"] == "上游失败"
    assert results[0]["elapsed_ms"] is not None, "失败也要留下耗时，便于排查"


def test_batch_timeout_marks_unfinished_sub_questions(monkeypatch):
    """编排器用整轮剩余预算收紧本批等待上限时的行为。"""
    import time as _time

    def slow(*args, **kwargs):
        """替身：睡 0.5s，用于触发批次等待超时分支。"""
        _time.sleep(0.5)
        return {"answer": "A", "coverage": "sufficient"}

    monkeypatch.setattr(orchestrator.llm, "invoke_json", slow)
    results = orchestrator.answer_sub_questions(
        {"sub_questions": [_sub(1, "a")]},
        "问题",
        _search([_knowledge_item(1, 1, 2)]),
        lambda *a: {},
        timeout=0.05,
    )
    assert results[0]["error"] == "子问题作答超时"


def _round_one_result(key_entities):
    """构造第一轮已完成的子问题结果（链式问题的依赖方）。"""
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
    """构造一个带占位符的待定子问题（链式第二跳）。"""
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
    """链式第二跳：占位符被上一跳的 key_entities 填实，并标记 follow_up_of。"""
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
    """缺口补查会把用户原问题作为备用查询（双查询交替取用提高召回）。"""
    plan = orchestrator.build_round_two_plan(
        [_round_one_result(["SC-300"])], [1], limit=8, question="原问题"
    )
    assert plan["sub_questions"][0]["alt_query"] == "原问题"


def test_round_two_adds_stripped_query_variant():
    """链式查询额外产出「去掉占位符的意图词」变体，避免实体替换后引入噪声。"""
    plan = orchestrator.build_round_two_plan(
        [_round_one_result(["SC-300"])],
        [],
        pending=_pending(),
        limit=8,
        question="原问题",
    )
    assert plan["sub_questions"][0]["stripped_query"] == "单体质量能量密度 25℃ 循环寿命"


def test_collect_evidence_searches_all_query_variants():
    """取证阶段按顺序检索全部查询变体（主查询 → 原问题 → 意图词），结果合并去重。"""
    searched: list[str] = []

    def search(query):
        """替身：记录查询串并返回不同的证据（doc_id 随调用序号变化）。"""
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
    """两个查询命中不同证据时合并注入，且不出现重复证据单元。"""
    primary = _knowledge_item(1, 10, 20, "a.md")
    alternative = _knowledge_item(2, 30, 40, "b.md")

    def search(query):
        """替身：按查询串返回不同证据（原问题命中 alternative）。"""
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
    """多个中间实体（≤2）各自展开一次检索，保持并列呈现而非只取第一个。"""
    result = _round_one_result(["SC-300", "SC-400"])
    plan = orchestrator.build_round_two_plan([result], [1], pending=None, limit=8)
    assert [item["query"] for item in plan["sub_questions"]] == ["SC-300", "SC-400"]


def test_round_two_marks_chain_and_gap_pools():
    """链式延伸进链式池，缺口补充进缺口池——两者额度独立。"""
    dependency = _round_one_result(["SC-300"])  # id=1，供链式待定项使用
    unresolved = _round_one_result(["SC-400"])  # id=2，覆盖不足 → 缺口补查
    unresolved["id"] = 2
    unresolved["coverage"] = "partial"
    plan = orchestrator.build_round_two_plan(
        [dependency, unresolved], [2], pending=_pending(), limit=8, question="原问题"
    )
    pools = [item["pool"] for item in plan["sub_questions"]]
    assert pools == ["chain", "gap"]


def test_pools_do_not_compete_for_budget(monkeypatch):
    """缺口池用尽后，链式池仍能拿到自己的额度。"""
    monkeypatch.setattr(Config, "AGENT_CHAIN_EVIDENCE_BUDGET", 2)
    monkeypatch.setattr(Config, "AGENT_GAP_EVIDENCE_BUDGET", 1)
    monkeypatch.setattr(Config, "AGENT_EVIDENCE_PER_SUB", 3)
    items = [_knowledge_item(index, index * 10, index * 10 + 2) for index in range(1, 7)]
    search = _search(items)

    gaps = [_sub(index, f"gap{index}", source="knowledge") for index in range(1, 3)]
    for item in gaps:
        item["pool"] = orchestrator.POOL_GAP
    chains = [_sub(9, "chain", source="knowledge")]
    chains[0]["pool"] = orchestrator.POOL_CHAIN

    state = orchestrator.new_evidence_state()
    per_sub = orchestrator.collect_evidence(gaps + chains, search, lambda *a: {}, state)

    gap_total = sum(len(per_sub[item["id"]]["items"]) for item in gaps)
    assert gap_total == 1, "缺口池只应拿 1 条"
    assert len(per_sub[9]["items"]) == 2, "链式池不受缺口池影响"
    assert state["chain"] == 2 and state["gap"] == 1


def test_chain_budget_scales_with_sub_question_count(monkeypatch):
    """链式池按需求动态计算：规划器多拆子问题不会再饿死排在后面的链式义务。"""
    monkeypatch.setattr(Config, "AGENT_EVIDENCE_PER_SUB", 3)
    monkeypatch.setattr(Config, "AGENT_CHAIN_EVIDENCE_BUDGET", 15)
    items = [
        {"source": "knowledge", "pool": orchestrator.POOL_CHAIN} for _ in range(4)
    ]
    assert orchestrator._effective_limits(items)["chain"] == 12

    # 安全阀仍然生效：子问题再多也不超过上限
    many = [
        {"source": "knowledge", "pool": orchestrator.POOL_CHAIN} for _ in range(9)
    ]
    assert orchestrator._effective_limits(many)["chain"] == 15


def test_pending_intent_duplicates_are_skipped():
    """意图相同的重复待定子问题只保留第一个，不再白占链式池额度。"""
    first = _pending()[0]
    second = dict(first, id=3, depends_on=2)
    dependency = _round_one_result(["SC-300"])
    other = _round_one_result(["SC-400"])
    other["id"] = 2
    plan = orchestrator.build_round_two_plan(
        [dependency, other],
        [],
        pending=[first, second],
        limit=8,
        question="原问题",
    )
    assert len(plan["sub_questions"]) == 1
    assert plan["sub_questions"][0]["query"].startswith("SC-300")
