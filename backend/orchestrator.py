"""增强模式编排（设计文档 7.8）。

本模块当前实现**规划步**：一次 LLM 调用产出「是否需要拆解 + 意图 + 结构化过滤条件
+ 子问题列表」，解析失败重试一次，仍失败则打回退标记，由上层退回标准模式单跳。
子答案并行、有界第二轮、逐项合成在后续提交中补齐。
"""

import logging
import json
import re
from concurrent.futures import ThreadPoolExecutor, wait

import agent_tools
import chroma_store
import llm
import order_qa
import rag
from config import Config

logger = logging.getLogger("orchestrator")

# 子问题的数据来源：知识检索 / 订单查询
SOURCE_KNOWLEDGE = "knowledge"
SOURCE_ORDER = "order"
SOURCES = (SOURCE_KNOWLEDGE, SOURCE_ORDER)

INTENTS = ("order", "knowledge", "mixed")
PLANNER_FALLBACK_PREFIX = "未能完成问题拆解，按标准模式回答。"

# 子问题证据覆盖状态
COVERAGE_SUFFICIENT = "sufficient"
COVERAGE_PARTIAL = "partial"
COVERAGE_MISSING = "missing"
COVERAGES = (COVERAGE_SUFFICIENT, COVERAGE_PARTIAL, COVERAGE_MISSING)

# 单次子答案调用的 token 预算。同样要给足：推理型模型会先花掉大量预算再输出
# JSON，实测 800 在证据较长（如年报片段）时会被耗尽并抛错，表现为子答案
# 空空如也、覆盖状态为缺失。取值走配置，便于按实际用量调整。
SUB_ANSWER_MAX_TOKENS = Config.AGENT_SUB_ANSWER_MAX_TOKENS

# 规划调用的 token 预算。不能沿用提供方的 router_max_tokens（默认 300）：
# 那是给标准模式的短路由提示词配的。实测 DeepSeek-V4-Flash 在链式问题上
# 300、1200 均被推理耗尽（返回空内容、finish_reason=length、触发回退），
# 3000 也只是压线通过，因此默认给到 10000 并走配置。
PLAN_MAX_TOKENS = Config.AGENT_PLAN_MAX_TOKENS

_PLAN_EXAMPLES = """示例1（并列型，同一实体的多个属性）：
用户：2025 年动力电池系统的营收 / 占比 / 毛利率 / 销量？
输出：{"needs_decomposition": true, "intent": "knowledge", "filters": {}, "aggregation": null,
      "sub_questions": [
        {"id": 1, "query": "2025 动力电池系统 营收", "source": "knowledge"},
        {"id": 2, "query": "2025 动力电池系统 占比", "source": "knowledge"},
        {"id": 3, "query": "2025 动力电池系统 毛利率", "source": "knowledge"},
        {"id": 4, "query": "2025 动力电池系统 销量", "source": "knowledge"}]}

示例2（链式型，第二跳的实体要等第一跳结果才知道；用 {id} 占位并标注 depends_on）：
用户：为 SC-500 工商业储能一体柜供电的那款电芯，它的单体质量能量密度和 25℃ 循环寿命分别是多少？
输出：{"needs_decomposition": true, "intent": "knowledge", "filters": {}, "aggregation": null,
      "sub_questions": [
        {"id": 1, "query": "SC-500 工商业储能一体柜 配套 电芯 型号", "source": "knowledge"},
        {"id": 2, "query": "{1} 单体质量能量密度 25℃ 循环寿命", "source": "knowledge",
         "depends_on": 1}]}

示例3（单跳，不拆解）：
用户：目前未完成的订单有几笔？
输出：{"needs_decomposition": false, "intent": "order", "filters": {"status": "pending"},
      "aggregation": "count", "sub_questions": []}"""


def build_plan_prompt(question: str, history: list | None = None) -> str:
    return (
        "你是企业知识系统的问答规划器。判断问题是否需要拆解，输出严格 JSON："
        '{"needs_decomposition": true|false, "intent": "order"|"knowledge"|"mixed", '
        '"filters": {...}, "aggregation": null|"count"|"sum"|"avg", '
        '"sub_questions": [{"id": 1, "query": "...", "source": "knowledge"|"order", '
        '"filters": {...}, "aggregation": null}]}。\n'
        "规则：\n"
        "1. 子问题的 query 写成「实体 + 属性 + 限定条件」的检索式，不得丢失量化条件"
        "（年度、温度、口径）；不要写成完整问句。\n"
        "2. 每个子问题必须标注数据来源：订单数据库查询用 order（并在该子问题内给出 "
        "filters/aggregation），知识库检索用 knowledge。\n"
        "3. 只有一个信息需求时 needs_decomposition 为 false，sub_questions 留空数组。\n"
        "4. 链式问题（后续步骤依赖前面结果的）分两步表达：第一步写成正常子问题；"
        "后续步骤写成待定子问题，query 里用 {前一步的 id} 占位表示未知实体，"
        "并标注 depends_on；系统会在拿到第一步的中间实体后填实占位再检索。\n"
        "5. 只抽取问题中明确给出的条件，不得脑补；拿不准一律按 knowledge 处理。\n"
        "只输出 JSON，不要输出任何其他文字。\n"
        f"{_PLAN_EXAMPLES}\n\n历史对话：\n{order_qa._format_history(history)}"
        f"用户问题：{question}"
    )


def _clean_filters(raw) -> dict:
    """过滤键只保留白名单内的，取值合法性由 order_qa 在执行时再校验。"""
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items() if key in order_qa.ALLOWED_FILTERS}


def _clean_aggregation(raw):
    return raw if raw in order_qa.ALLOWED_AGGREGATIONS else None


def _sanitize_sub_question(raw, index: int) -> dict | None:
    if not isinstance(raw, dict):
        return None
    query = (raw.get("query") or raw.get("text") or "").strip()
    if not query:
        return None
    source = raw.get("source")
    if source not in SOURCES:
        source = SOURCE_KNOWLEDGE
    depends_on = raw.get("depends_on")
    if not isinstance(depends_on, int) or isinstance(depends_on, bool):
        depends_on = None
    return {
        "id": index,
        "query": query,
        "source": source,
        "filters": _clean_filters(raw.get("filters")),
        "aggregation": _clean_aggregation(raw.get("aggregation")),
        "depends_on_raw": depends_on,
    }


def _normalize(data: dict) -> dict:
    intent = data.get("intent")
    if intent not in INTENTS:
        raise ValueError(f"非法 intent：{intent}")

    raw_sub_questions = data.get("sub_questions") or []
    if not isinstance(raw_sub_questions, list):
        raise ValueError("sub_questions 必须为数组")

    sub_questions: list[dict] = []
    raw_to_assigned: dict[int, int] = {}
    for raw in raw_sub_questions:
        item = _sanitize_sub_question(raw, len(sub_questions) + 1)
        if item is None:
            continue
        raw_id = raw.get("id") if isinstance(raw, dict) else None
        if isinstance(raw_id, int) and not isinstance(raw_id, bool):
            raw_to_assigned[raw_id] = item["id"]
        sub_questions.append(item)

    # 依赖解析：模型可能用自己给的编号，也可能用本系统的编号
    assigned_ids = {item["id"] for item in sub_questions}
    for item in sub_questions:
        dependency = item.pop("depends_on_raw")
        mapped = raw_to_assigned.get(dependency, dependency)
        item["depends_on"] = (
            mapped
            if mapped in assigned_ids and mapped != item["id"] and dependency is not None
            else None
        )

    limit = Config.AGENT_MAX_SUB_QUESTIONS
    truncated = max(0, len(sub_questions) - limit)
    if truncated:
        logger.warning("子问题超过执行上限 %s，截断 %s 个", limit, truncated)

    return {
        "needs_decomposition": bool(
            data.get("needs_decomposition", bool(sub_questions))
        ),
        "intent": intent,
        "filters": _clean_filters(data.get("filters")),
        "aggregation": _clean_aggregation(data.get("aggregation")),
        "sub_questions": sub_questions[:limit],
        "truncated": truncated,
        "fallback": False,
    }


def plan_question(question: str, history: list | None = None) -> dict:
    """规划一次；解析失败重试一次，仍失败返回 fallback 标记。"""
    prompt = build_plan_prompt(question, history)
    for attempt in range(2):
        try:
            return _normalize(llm.invoke_json(prompt, max_tokens=PLAN_MAX_TOKENS))
        except Exception as exc:  # noqa: BLE001 - 规划失败需回退而非中断
            logger.warning("规划解析失败（第 %s 次）：%s", attempt + 1, exc)
    return {
        "needs_decomposition": False,
        "intent": "knowledge",
        "filters": {},
        "aggregation": None,
        "sub_questions": [],
        "truncated": 0,
        "fallback": True,
    }


# ── 子答案：证据裁决 + 并行作答 ────────────────────────────────────────────


def build_sub_answer_prompt(
    question: str, sub_question: dict, evidence_text: str
) -> str:
    return (
        "你是企业知识系统的子问题回答器。只回答给定的子问题，不要扩展、不要替用户"
        "回答别的问题。\n"
        "参考资料不足以回答时，把 coverage 设为 partial 或 missing，不得编造；"
        "答案中的数值必须与参考资料逐字一致。\n"
        "若参考资料给出了可供后续检索的关键实体（如型号、编号），放进 key_entities。\n"
        "输出严格 JSON："
        '{"answer": "...", "coverage": "sufficient|partial|missing", '
        '"evidence_ids": [1], "key_entities": ["..."]}\n'
        "只输出 JSON，不要输出任何其他文字。\n\n"
        f"原始问题（仅供理解语境）：{question}\n"
        f"本次要回答的子问题：{sub_question['query']}\n\n"
        f"参考资料：\n{evidence_text or '（无）'}"
    )


def _format_knowledge_evidence(items: list[dict]) -> str:
    lines = []
    for index, item in enumerate(items, start=1):
        lines.append(
            f"[{index}] {item.get('filename', '')}"
            f"（{item.get('domain', '')}，第 {item.get('parent_start_line')}-"
            f"{item.get('parent_end_line')} 行）\n{item.get('text', '')}"
        )
    return "\n\n".join(lines)


def new_evidence_state() -> dict:
    """跨轮共享的证据预算：全局计数与已占用的证据单元。"""
    return {"seen": set(), "total": 0}


def collect_evidence(
    sub_questions: list[dict], search_tool, order_tool, state: dict | None = None
) -> dict:
    """按每子问题配额与全局上限裁决证据，跨子问题按证据单元去重。"""
    state = state if state is not None else new_evidence_state()
    per_sub: dict[int, dict] = {}
    per_limit = Config.AGENT_EVIDENCE_PER_SUB
    global_limit = Config.AGENT_EVIDENCE_GLOBAL

    for sub_question in sub_questions:
        if sub_question["source"] == SOURCE_ORDER:
            per_sub[sub_question["id"]] = {
                "kind": SOURCE_ORDER,
                "result": order_tool(
                    sub_question.get("filters") or {}, sub_question.get("aggregation")
                ),
                "items": [],
            }
            continue

        # 第二轮的子问题可能带备用查询（通常是用户原问题）：规划器写占位查询时
        # 还不知道实体，措辞容易偏离原文，两个查询交替取用可兼顾精确与召回。
        queries = [sub_question["query"]]
        for extra in (
            sub_question.get("alt_query"),
            sub_question.get("stripped_query"),
        ):
            if extra and _normalize_query(extra) not in {
                _normalize_query(item) for item in queries
            }:
                queries.append(extra)

        first_result: dict = {"status": agent_tools.STATUS_EMPTY, "items": []}
        pools: list[list[dict]] = []
        for position, query in enumerate(queries):
            result = search_tool(query)
            if position == 0:
                first_result = result
            pools.append(list(result.get("items") or []))

        items: list[dict] = []
        while len(items) < per_limit and any(pools):
            progressed = False
            for pool in pools:
                if len(items) >= per_limit or state["total"] >= global_limit:
                    break
                while pool:
                    candidate = pool.pop(0)
                    key = chroma_store.evidence_unit_key(
                        candidate.get("doc_id"),
                        candidate.get("parent_start_line"),
                        candidate.get("parent_end_line"),
                    )
                    if key in state["seen"]:
                        continue
                    state["seen"].add(key)
                    state["total"] += 1
                    items.append(candidate)
                    progressed = True
                    break
            if not progressed:
                break
        per_sub[sub_question["id"]] = {
            "kind": SOURCE_KNOWLEDGE,
            "result": first_result,
            "items": items,
        }

    return per_sub


def _base_result(sub_question: dict) -> dict:
    return {
        "id": sub_question["id"],
        "query": sub_question["query"],
        "source": sub_question["source"],
        "answer": "",
        "coverage": COVERAGE_MISSING,
        "evidence_ids": [],
        "key_entities": [],
        "evidence": [],
        "order": None,
        "error": None,
    }


def _normalize_sub_answer(data: dict) -> dict:
    answer = (data.get("answer") or "").strip()
    coverage = data.get("coverage")
    if coverage not in COVERAGES:
        coverage = COVERAGE_SUFFICIENT if answer else COVERAGE_MISSING
    evidence_ids = [
        value
        for value in (data.get("evidence_ids") or [])
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    key_entities = [
        str(value).strip()
        for value in (data.get("key_entities") or [])
        if str(value).strip()
    ][:2]
    return {
        "answer": answer,
        "coverage": coverage,
        "evidence_ids": evidence_ids,
        "key_entities": key_entities,
    }


def answer_sub_questions(
    plan: dict,
    question: str,
    search_tool,
    order_tool,
    history: list | None = None,
    state: dict | None = None,
) -> list[dict]:
    """并行取得每个子问题的子答案；单个子问题失败不影响其他子问题。"""
    sub_questions = plan.get("sub_questions") or []
    if not sub_questions:
        return []

    evidence_map = collect_evidence(sub_questions, search_tool, order_tool, state)
    results = [_base_result(sub_question) for sub_question in sub_questions]

    def run(index: int) -> dict:
        sub_question = sub_questions[index]
        evidence = evidence_map[sub_question["id"]]
        result = _base_result(sub_question)
        result["evidence"] = evidence["items"]
        if evidence["kind"] == SOURCE_ORDER:
            order_result = evidence["result"]
            result["order"] = order_result
            if order_result.get("status") == "denied":
                return result
            evidence_text = order_qa.format_order_context(order_result)
        else:
            evidence_text = _format_knowledge_evidence(evidence["items"])
            if not evidence["items"]:
                return result

        prompt = build_sub_answer_prompt(question, sub_question, evidence_text)
        result.update(
            _normalize_sub_answer(
                llm.invoke_json(prompt, max_tokens=SUB_ANSWER_MAX_TOKENS)
            )
        )
        return result

    with ThreadPoolExecutor(
        max_workers=max(1, min(len(sub_questions), Config.AGENT_MAX_SUB_QUESTIONS)),
        thread_name_prefix="agent-sub",
    ) as executor:
        futures = {
            executor.submit(run, index): index for index in range(len(sub_questions))
        }
        done, not_done = wait(futures.keys(), timeout=Config.AGENT_SUB_TIMEOUT)
        for future in done:
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # noqa: BLE001 - 单个子问题失败不阻塞其他
                logger.warning("子问题 %s 作答失败：%s", index + 1, exc)
                results[index]["error"] = str(exc)
        for future in not_done:
            index = futures[future]
            logger.warning("子问题 %s 超时", index + 1)
            results[index]["error"] = "子问题作答超时"

    return results


# ── 合成：按子问题逐项落位 ────────────────────────────────────────────────

NO_EVIDENCE_NOTE = "未在知识库中找到依据"
MISSING_ANSWER_NOTE = "该子问题未能作答"

_COVERAGE_LABEL = {
    COVERAGE_SUFFICIENT: "证据充分",
    COVERAGE_PARTIAL: "证据部分缺失",
    COVERAGE_MISSING: "证据缺失",
}


def unresolved_sub_questions(sub_results: list[dict]) -> list[int]:
    """仍未拿到充分证据（或作答失败）的子问题编号，供合成与提示使用。"""
    return [
        item["id"]
        for item in sub_results
        if item["coverage"] != COVERAGE_SUFFICIENT or item["error"]
    ]


def build_synthesis_prompt(
    question: str, sub_results: list[dict], history: list | None = None
) -> str:
    blocks: list[str] = []
    for item in sub_results:
        if item["error"]:
            state = f"作答失败（{item['error']}）"
        else:
            state = _COVERAGE_LABEL.get(item["coverage"], item["coverage"])
        block = [
            f"【子问题 {item['id']}】{item['query']}",
            f"状态：{state}",
            f"子答案：{item['answer'] or '（无）'}",
        ]
        if item["evidence"]:
            block.append("依据：" + _format_knowledge_evidence(item["evidence"]))
        elif item.get("order") and item["order"].get("status") == "ok":
            block.append("依据：" + order_qa.format_order_context(item["order"]))
        blocks.append("\n".join(block))

    return (
        "你是星辰科技集团的内部知识助手。下面是系统对同一个问题的分项调查结果，"
        "请据此给出最终回答。\n"
        "要求：\n"
        "1. 按子问题逐项落位，每一项都要有交代；状态为证据缺失或作答失败的子问题，"
        f"必须写明「{NO_EVIDENCE_NOTE}」，不得猜测、不得用常识补全。\n"
        "2. 子答案只是线索，事实以「依据」中的原文为准；回答里出现的数值必须能在依据中"
        "逐字找到，不要四舍五入或换算单位。\n"
        "3. 不要罗列子问题编号，用自然的表达组织答案。\n\n"
        f"{order_qa._format_history(history)}"
        f"用户问题：{question}\n\n分项调查结果：\n"
        + "\n\n".join(blocks)
    )


def synthesize(
    question: str, sub_results: list[dict], history: list | None = None
) -> str:
    """非流式合成；流式输出在接口层用同一份 prompt 走 llm.stream。"""
    return llm.invoke(build_synthesis_prompt(question, sub_results, history))


# ── 有界第二轮与编排入口 ──────────────────────────────────────────────────


def _event(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _fill_placeholder(query: str, dependency_id, entity: str) -> str:
    placeholder = "{" + str(dependency_id) + "}"
    if placeholder in query:
        return query.replace(placeholder, entity)
    return f"{entity} {query}"


def _strip_placeholders(query: str) -> str:
    """去掉占位符，只保留意图词。

    规划器写占位查询时还不知道实体，替换后可能把实体变成噪声——例如
    「{1} 年报释义 正式全称」替换成「SC-300 年报释义 正式全称」，而年报的
    释义表里根本没有「SC-300」这个词。留一个不带实体的变体作为第三种查询。
    """
    return " ".join(re.sub(r"\{\s*\d+\s*\}", " ", query or "").split())


def _normalize_query(query: str) -> str:
    return " ".join((query or "").split()).lower()


def _is_redundant(query: str, existing: list[str]) -> bool:
    """同一轮内已经查过（或被更宽的查询覆盖）就不再重复检索。

    只与**本轮**已加入的查询比对：与历史查询做包含判断会误伤真正的细化检索
    （例如第一轮查「SC-300」、第二轮要查「SC-300 循环寿命」）。
    """
    normalized = _normalize_query(query)
    return any(
        normalized == item or normalized in item or item in normalized
        for item in existing
    )


def build_round_two_plan(
    sub_results: list[dict],
    unresolved_ids: list[int],
    pending: list[dict] | None = None,
    limit: int | None = None,
    question: str | None = None,
) -> dict:
    """第二轮计划，由两部分组成：

    1. **链式延伸**：规划阶段留下的待定子问题（带 depends_on），用依赖子问题的
       中间实体填实占位符后执行——这正是链式问题的第二跳。
    2. **缺口补充**：对覆盖为部分/缺失的子问题，直接用其关键实体再查一轮。

    待定项若无法展开（依赖无结果或没有中间实体），优先回退用**用户原问题**检索一次
    （原问题的措辞通常比规划器事先写好的占位查询更贴原文）；连原问题都没有时，
    才在 unresolved_pending 里返回，由调用方作为「证据缺失」写入结果，避免静默漏项。
    """
    sub_questions: list[dict] = []
    unresolved_pending: list[dict] = []
    added_queries: list[str] = []
    offset = len(sub_results)
    results_by_id = {item["id"]: item for item in sub_results}
    fallback_used = False

    def add(query: str, follow_up_of, alt_query: str | None = None) -> None:
        added_queries.append(_normalize_query(query))
        sub_questions.append(
            {
                "id": offset + len(sub_questions) + 1,
                "query": query,
                "source": SOURCE_KNOWLEDGE,
                "filters": {},
                "aggregation": None,
                "follow_up_of": follow_up_of,
                "alt_query": alt_query,
            }
        )

    for item in pending or []:
        dependency = results_by_id.get(item.get("depends_on"))
        entities = dependency["key_entities"] if dependency else []
        if not entities:
            # 拿不到中间实体：优先用该子问题**去掉占位符的意图词**检索
            # （它比整条原问题更贴近文档用词），实在没有再退回原问题
            intent_terms = _strip_placeholders(item["query"])
            fallback_query = intent_terms or question or ""
            if fallback_query and not fallback_used and not _is_redundant(
                fallback_query, added_queries
            ):
                fallback_used = True
                add(fallback_query, item.get("depends_on"), alt_query=question)
            else:
                unresolved_pending.append(item)
            continue
        for entity in entities[:2]:
            if limit is not None and len(sub_questions) >= limit:
                unresolved_pending.append(item)
                break
            add(
                _fill_placeholder(item["query"], item.get("depends_on"), entity),
                item.get("depends_on"),
                alt_query=question,
            )
            sub_questions[-1]["stripped_query"] = _strip_placeholders(item["query"])

    for item in sub_results:
        if item["id"] not in unresolved_ids:
            continue
        for entity in item["key_entities"][:2]:
            if limit is not None and len(sub_questions) >= limit:
                return {
                    "sub_questions": sub_questions,
                    "unresolved_pending": unresolved_pending,
                }
            if _is_redundant(entity, added_queries):
                continue
            add(entity, item["id"], alt_query=question)

    return {
        "sub_questions": sub_questions,
        "unresolved_pending": unresolved_pending,
    }


def collect_sources(sub_results: list[dict]) -> list[dict]:
    """按子问题分组来源：每条来源带 sub_question_id，供前端分区渲染。"""
    sources: list[dict] = []
    for item in sub_results:
        for evidence in item["evidence"]:
            sources.append(
                {
                    "source_type": "vector",
                    "filename": evidence.get("filename", ""),
                    "domain": evidence.get("domain", ""),
                    "content_preview": (evidence.get("text") or "")[:200],
                    "doc_id": evidence.get("doc_id"),
                    "chunk_id": evidence.get("chunk_id"),
                    "chunk_type": evidence.get("chunk_type"),
                    "start_line": evidence.get("start_line"),
                    "sub_question_id": item["id"],
                }
            )
        order_result = item.get("order")
        if order_result and order_result.get("status") == "ok":
            source = order_qa.build_database_source(order_result)
            source["sub_question_id"] = item["id"]
            sources.append(source)
    return sources


def build_trace(
    plan: dict,
    sub_results: list[dict],
    round_two_count: int = 0,
    single_hop: bool = False,
    fallback: bool = False,
) -> dict:
    return {
        "single_hop": single_hop,
        "fallback": fallback,
        "truncated_sub_questions": plan.get("truncated", 0),
        "rounds": 2 if round_two_count else 1,
        "round_two_sub_questions": round_two_count,
        "sub_questions": [
            {
                "id": item["id"],
                "query": item["query"],
                "source": item["source"],
                "answer": item["answer"],
                "coverage": item["coverage"],
                "error": item["error"],
                "key_entities": item["key_entities"],
                "evidence": [
                    {
                        "doc_id": evidence.get("doc_id"),
                        "parent_start_line": evidence.get("parent_start_line"),
                        "parent_end_line": evidence.get("parent_end_line"),
                    }
                    for evidence in item["evidence"]
                ],
            }
            for item in sub_results
        ],
    }


def run_enhanced(
    question: str,
    user_role: str,
    history: list | None = None,
    trace_out: dict | None = None,
):
    """编排入口：产出 JSON 事件字符串（stage / token / done）。

    trace 通过 trace_out 回传——生成器无法 return 值，接口层传入一个字典，
    消费完事件流后即可拿到过程记录并随消息落库。
    """
    search_tool = agent_tools.build_knowledge_search(
        user_role, k=Config.AGENT_RETRIEVE_K
    )
    order_tool = agent_tools.build_order_query(user_role)

    yield _event({"stage": "planning"})
    plan = plan_question(question, history)

    if plan["fallback"]:
        # 规划失败：退回标准模式单跳，并显式告诉用户（故障不静默）
        if trace_out is not None:
            trace_out.update(build_trace(plan, [], single_hop=True, fallback=True))
        yield _event({"token": PLANNER_FALLBACK_PREFIX})
        for event in rag.answer_question(
            question, history=history, user_role=user_role, stream=True
        ):
            yield event
        return

    if not plan["needs_decomposition"]:
        # 单跳短路：界面不做任何提示，只在 trace 里留痕
        if trace_out is not None:
            trace_out.update(build_trace(plan, [], single_hop=True))
        for event in rag.answer_question(
            question, history=history, user_role=user_role, stream=True
        ):
            yield event
        return

    sub_questions = plan["sub_questions"]
    pending = [item for item in sub_questions if item.get("depends_on")]
    immediate = [item for item in sub_questions if not item.get("depends_on")]
    if not immediate:
        # 规划只给了依赖子问题（异常情况）：全部按第一轮执行，避免空转
        immediate, pending = sub_questions, []
    yield _event(
        {
            "stage": "planned",
            "sub_questions": [
                {
                    "id": item["id"],
                    "text": item["query"],
                    "depends_on": item.get("depends_on"),
                }
                for item in sub_questions
            ],
        }
    )

    state = new_evidence_state()
    results = answer_sub_questions(
        {"sub_questions": immediate},
        question,
        search_tool,
        order_tool,
        history,
        state,
    )
    for item in results:
                yield _event(
                    {
                        "stage": "sub_answer",
                        "sub_question_id": item["id"],
                        "answer": item["answer"],
                        "coverage": item["coverage"],
                        "error": item["error"],
                    }
                )

    round_two_count = 0
    unresolved = unresolved_sub_questions(results)
    round_two_plan = build_round_two_plan(
        results,
        unresolved,
        pending=pending,
        limit=Config.AGENT_MAX_SUB_QUESTIONS,
        question=question,
    )
    # 连原问题回退都没能展开的待定项：按证据缺失写入结果，并发事件告知前端，
    # 避免出现「无声消失的子问题」
    for item in round_two_plan["unresolved_pending"]:
        results.append(_base_result(item))
        yield _event(
            {
                "stage": "sub_answer",
                "sub_question_id": item["id"],
                "answer": "",
                "coverage": COVERAGE_MISSING,
                "error": "依赖未满足，未执行检索",
                "follow_up": True,
            }
        )
    if round_two_plan["sub_questions"]:
        round_two_count = len(round_two_plan["sub_questions"])
        extra_results = answer_sub_questions(
            round_two_plan, question, search_tool, order_tool, history, state
        )
        results.extend(extra_results)
        for item in extra_results:
            yield _event(
                {
                    "stage": "sub_answer",
                    "sub_question_id": item["id"],
                    "answer": item["answer"],
                    "coverage": item["coverage"],
                    "error": item["error"],
                    "follow_up": True,
                }
            )

    if trace_out is not None:
        trace_out.update(
            build_trace(plan, results, round_two_count=round_two_count)
        )

    yield _event({"stage": "synthesizing"})
    prompt = build_synthesis_prompt(question, results, history)
    for token in llm.stream(prompt, max_tokens=Config.AGENT_SYNTHESIS_MAX_TOKENS):
        yield _event({"token": token})
    yield _event({"done": True, "sources": collect_sources(results)})
