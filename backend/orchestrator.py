"""增强模式编排（设计文档 7.8）。

链路：规划（一次 LLM 调用）→ 第一轮子问题并行检索作答 → 有界第二轮（链式池 +
缺口池）→ 逐项合成流式输出。规划连续两次解析失败、整轮时间预算耗尽、全部子问题
作答失败、合成未吐字即失败，四种情况都退回标准模式单跳并显式说明；全部子问题被
订单工具拒绝时不调用 LLM，直接返回固定话术。
"""

import logging
import json
import re
import time
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
# 以下三种回退都不静默：先以 token 形式说明原因，再输出标准模式单跳的回答
SUB_ANSWER_FALLBACK_PREFIX = "子问题作答全部失败，按标准模式回答。"
SYNTHESIS_FALLBACK_PREFIX = "回答合成失败，按标准模式回答。"
BUDGET_FALLBACK_PREFIX = "增强模式超出整轮时间预算，按标准模式回答。"

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
    """规划提示词：判定意图 + 抽过滤条件 + 拆子问题，并给出并列/链式/单跳三类示例。

    规则里最要紧的两条：子问题写成「实体 + 属性 + 限定条件」的检索式（不丢年度/温度
    等量化条件）；链式问题用 `{前一步 id}` 占位 + depends_on 表达第二跳。
    """
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
    """聚合方式白名单过滤：非 count/sum/avg 一律当作「不聚合」。"""
    return raw if raw in order_qa.ALLOWED_AGGREGATIONS else None


def _sanitize_sub_question(raw, index: int) -> dict | None:
    """清洗单个子问题：query 必填、source 非法降级为 knowledge、过滤键走白名单。

    id 一律由编排器按顺序重新编号，不采信模型给的编号（链式依赖会因此错位）；
    原始 depends_on 暂存为 depends_on_raw，等全部子问题编号确定后再解析。
    """
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
    """把模型输出的规划 JSON 规整成内部契约（规划输出一律视为不可信输入）。

    职责：校验 intent、逐条清洗子问题、把模型自编的依赖编号映射到本系统编号、
    按 `AGENT_MAX_SUB_QUESTIONS` 截断并记录截断数。
    """
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
    usage: dict = {}
    for attempt in range(2):
        try:
            plan = _normalize(
                llm.invoke_json(prompt, max_tokens=PLAN_MAX_TOKENS, usage=usage)
            )
            plan["usage"] = usage
            return plan
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
        "usage": usage,
    }


# ── 子答案：证据裁决 + 并行作答 ────────────────────────────────────────────


def build_sub_answer_prompt(
    question: str, sub_question: dict, evidence_text: str
) -> str:
    """子答案提示词：只答当前子问题、数值逐字引用、证据不足要标 coverage。

    同时要求模型给出可供下一轮检索的 key_entities（链式第二跳靠它填占位符）。
    """
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
    """把证据单元编号排版成 `[n] 文件名（领域，起止行）` + 原文，供提示词引用。

    编号是子答案与合成阶段引用依据的锚点（evidence_ids）。
    """
    lines = []
    for index, item in enumerate(items, start=1):
        lines.append(
            f"[{index}] {item.get('filename', '')}"
            f"（{item.get('domain', '')}，第 {item.get('parent_start_line')}-"
            f"{item.get('parent_end_line')} 行）\n{item.get('text', '')}"
        )
    return "\n\n".join(lines)


def new_evidence_state() -> dict:
    """跨轮共享的状态：去重集合全局唯一，计数按预算池分开（ADR 0010）。"""
    return {"seen": set(), "round1": 0, "chain": 0, "gap": 0}


# 证据预算池：第一轮、链式延伸、缺口补充各自独立计数，互不借用
POOL_ROUND_ONE = "round1"
POOL_CHAIN = "chain"
POOL_GAP = "gap"


def _pool_limit(pool: str, state: dict | None = None) -> int:
    """取某个池的额度：优先用本轮算好的动态限额，没有则回退配置默认值。"""
    if state is not None and pool in state.get("limits", {}):
        return state["limits"][pool]
    if pool == POOL_CHAIN:
        return Config.AGENT_CHAIN_EVIDENCE_BUDGET
    if pool == POOL_GAP:
        return Config.AGENT_GAP_EVIDENCE_BUDGET
    return Config.AGENT_EVIDENCE_GLOBAL


def _effective_limits(sub_questions: list[dict]) -> dict:
    """池额度：链式池按需求动态计算——每个链式子问题都要拿到完整配额，
    否则规划器多拆一个子问题就会把排在后面的链式义务饿死；安全阀负责封顶，
    避免子问题很多时把提示词撑爆。缺口池保持固定（它是机会性补查）。"""
    knowledge_items = [item for item in sub_questions if item["source"] != SOURCE_ORDER]
    chain_count = sum(
        1 for item in knowledge_items if item.get("pool") == POOL_CHAIN
    )
    chain_limit = min(
        Config.AGENT_EVIDENCE_PER_SUB * max(1, chain_count),
        Config.AGENT_CHAIN_EVIDENCE_BUDGET,
    )
    limits = {
        POOL_ROUND_ONE: Config.AGENT_EVIDENCE_GLOBAL,
        POOL_GAP: Config.AGENT_GAP_EVIDENCE_BUDGET,
    }
    if chain_count:
        # 只在真的存在链式项时写入，避免被后续批次覆盖成 0 项对应的额度
        limits[POOL_CHAIN] = chain_limit
    return limits


def _interleave(
    pools: list[list[dict]], per_limit: int, state: dict, pool: str
) -> list[dict]:
    """按查询变体轮转取用，受每子问题配额与本池额度双重约束。"""
    items: list[dict] = []
    limit = _pool_limit(pool, state)
    while len(items) < per_limit and state[pool] < limit and any(pools):
        progressed = False
        for candidates in pools:
            if len(items) >= per_limit or state[pool] >= limit:
                break
            while candidates:
                candidate = candidates.pop(0)
                key = chroma_store.evidence_unit_key(
                    candidate.get("doc_id"),
                    candidate.get("parent_start_line"),
                    candidate.get("parent_end_line"),
                    candidate.get("chunk_id"),
                )
                if key in state["seen"]:
                    continue
                state["seen"].add(key)
                state[pool] += 1
                items.append(candidate)
                progressed = True
                break
        if not progressed:
            break
    return items


def collect_evidence(
    sub_questions: list[dict], search_tool, order_tool, state: dict | None = None
) -> dict:
    """按每子问题配额与池额度裁决证据，跨子问题按证据单元去重。

    额度按「剩余额度 / 剩余子问题数」公平分配，而不是让前面的子问题先吃满自己的
    配额——否则池额度偏紧时，排在后面的子问题（往往正是链式义务）一条也拿不到。
    """
    state = state if state is not None else new_evidence_state()
    state.setdefault("limits", {}).update(_effective_limits(sub_questions))
    per_sub: dict[int, dict] = {}
    per_limit = Config.AGENT_EVIDENCE_PER_SUB
    global_limit = Config.AGENT_EVIDENCE_GLOBAL

    knowledge_count = sum(
        1 for item in sub_questions if item["source"] != SOURCE_ORDER
    )
    processed_knowledge = 0

    for sub_question in sub_questions:
        if sub_question["source"] == SOURCE_ORDER:
            per_sub[sub_question["id"]] = {
                "kind": SOURCE_ORDER,
                "result": order_tool(
                    sub_question.get("filters") or {}, sub_question.get("aggregation")
                ),
                "items": [],
                "variants": [],
                "pool": sub_question.get("pool", POOL_ROUND_ONE),
            }
            continue

        # 第二轮的子问题可能带备用查询（通常是用户原问题）：规划器写占位查询时
        # 还不知道实体，措辞容易偏离原文，两个查询交替取用可兼顾精确与召回。
        queries = [("primary", sub_question["query"])]
        for extra in (
            sub_question.get("alt_query"),
            sub_question.get("stripped_query"),
        ):
            if extra and _normalize_query(extra) not in {
                _normalize_query(item[1]) for item in queries
            }:
                queries.append(("alt_question" if extra == sub_question.get("alt_query") else "intent_terms", extra))

        first_result: dict = {"status": agent_tools.STATUS_EMPTY, "items": []}
        pools: list[list[dict]] = []
        variant_metrics: list[dict] = []
        for position, (kind, query) in enumerate(queries):
            result = search_tool(query)
            if position == 0:
                first_result = result
            pools.append(list(result.get("items") or []))
            variant_metrics.append(
                {"kind": kind, "query": query, **(result.get("retrieval") or {})}
            )

        pool = sub_question.get("pool", POOL_ROUND_ONE)
        processed_knowledge += 1
        remaining_items = max(1, knowledge_count - processed_knowledge + 1)
        budget_left = max(0, _pool_limit(pool, state) - state[pool])
        allowance = max(
            1, min(per_limit, -(-budget_left // remaining_items))
        )
        items = _interleave(pools, allowance, state, pool)
        per_sub[sub_question["id"]] = {
            "kind": SOURCE_KNOWLEDGE,
            "result": first_result,
            "items": items,
            "variants": variant_metrics,
            "pool": pool,
        }

    return per_sub


def _base_result(sub_question: dict) -> dict:
    """子问题结果骨架：先填满所有字段（缺省为空/未作答），调用方只覆盖拿到的部分。

    保证后续 trace、合成与前端事件读到的结构稳定，不必到处 get(...) 兜底。
    """
    return {
        "id": sub_question["id"],
        "query": sub_question["query"],
        "source": sub_question["source"],
        "pool": sub_question.get("pool", POOL_ROUND_ONE),
        "answer": "",
        "coverage": COVERAGE_MISSING,
        "evidence_ids": [],
        "key_entities": [],
        "evidence": [],
        "retrieval": [],
        "order": None,
        "error": None,
        "elapsed_ms": None,
        "usage": {},
    }


def _normalize_sub_answer(data: dict) -> dict:
    """规整子答案 JSON：coverage 非法时按「有答案即 sufficient」兜底，实体最多取 2 个。

    多候选场景下 2 个是「并列呈现」的上限，超出只当作歧义、不做猜测（设计文档 7.8）。
    """
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
    timeout: float | None = None,
) -> list[dict]:
    """并行取得每个子问题的子答案；单个子问题失败不影响其他子问题。

    timeout：本批等待上限（秒），默认 `AGENT_SUB_TIMEOUT`；编排器会用整轮剩余
    预算进一步收紧，避免一批等待吃掉整个循环的时间预算。
    """
    sub_questions = plan.get("sub_questions") or []
    if not sub_questions:
        return []

    evidence_map = collect_evidence(sub_questions, search_tool, order_tool, state)
    results = [_base_result(sub_question) for sub_question in sub_questions]
    wait_timeout = Config.AGENT_SUB_TIMEOUT if timeout is None else max(0.0, timeout)

    def run(index: int) -> dict:
        """单个子问题的完整处理：取证据 → 组装提示词 → 调用 LLM → 记录耗时与用量。

        异常在此吞掉并写入 result["error"]，让其它子问题不受影响（失败隔离）。
        """
        started = time.monotonic()
        result = _base_result(sub_questions[index])
        try:
            sub_question = sub_questions[index]
            evidence = evidence_map[sub_question["id"]]
            result["evidence"] = evidence["items"]
            result["retrieval"] = evidence.get("variants") or []
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
            usage: dict = {}
            result.update(
                _normalize_sub_answer(
                    llm.invoke_json(
                        prompt, max_tokens=SUB_ANSWER_MAX_TOKENS, usage=usage
                    )
                )
            )
            result["usage"] = usage
            return result
        except Exception as exc:  # noqa: BLE001 - 单个子问题失败不阻塞其他
            logger.warning("子问题 %s 作答失败：%s", index + 1, exc)
            result["error"] = str(exc)
            return result
        finally:
            # 失败也要留下耗时：排查「时好时坏」时先看这里
            result["elapsed_ms"] = int((time.monotonic() - started) * 1000)

    with ThreadPoolExecutor(
        max_workers=max(1, min(len(sub_questions), Config.AGENT_MAX_SUB_QUESTIONS)),
        thread_name_prefix="agent-sub",
    ) as executor:
        futures = {
            executor.submit(run, index): index for index in range(len(sub_questions))
        }
        done, not_done = wait(futures.keys(), timeout=wait_timeout)
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
    """合成提示词：按子问题逐项落位，缺项显式写「未在知识库中找到依据」。

    强调两点：子答案只是线索、事实必须以「依据」原文为准；数值必须逐字可查，
    不许四舍五入或换算单位——这是抑制数值幻觉的最后一道闸。
    """
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
    """把编排事件序列化成 SSE data 帧（与标准模式共用同一种线格式）。"""
    return json.dumps(payload, ensure_ascii=False)


def _fill_placeholder(query: str, dependency_id, entity: str) -> str:
    """把链式查询里的 `{id}` 占位符替换成上一跳得到的实体。

    占位符不存在时退化为「实体 + 查询」拼接：规划器偶尔会忘记写占位符，
    拼接至少能把实体带进检索式，比丢弃这一跳好。
    """
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
    """查询归一化（压空白 + 小写），只用于查重比较，不回写实际检索式。"""
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
    seen_intents: set = set()
    offset = len(sub_results)
    results_by_id = {item["id"]: item for item in sub_results}
    fallback_used = False

    def add(
        query: str,
        follow_up_of,
        alt_query: str | None = None,
        pool: str = POOL_GAP,
    ) -> None:
        """登记一个第二轮子问题：自动编号、记录备用查询与所属预算池。"""
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
                "pool": pool,
            }
        )

    for item in pending or []:
        intent_key = _normalize_query(_strip_placeholders(item["query"]))
        if intent_key and intent_key in seen_intents:
            # 同一意图的重复待定项（例如「SC-500 年报释义 正式全称」与
            # 「SC-300 年报释义 正式全称」）只保留第一个，避免无谓占用链式池
            logger.info("待定子问题意图重复，跳过：%s", item["query"])
            continue
        if intent_key:
            seen_intents.add(intent_key)
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
                add(
                    fallback_query,
                    item.get("depends_on"),
                    alt_query=question,
                    pool=POOL_CHAIN,
                )
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
                pool=POOL_CHAIN,
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


def _model_info() -> dict:
    """当前模型标识（trace 用）；取不到时留空，不影响回答。"""
    try:
        provider = llm.get_active_provider()
    except Exception:  # noqa: BLE001 - trace 记录不得影响问答
        return {}
    return {
        "id": getattr(provider, "id", None),
        "model": getattr(provider, "model", None),
    }


def _budget_snapshot(state: dict | None) -> dict:
    """证据预算池用量（trace 用）。"""
    state = state or {}
    return {
        "round1": state.get("round1", 0),
        "chain": state.get("chain", 0),
        "gap": state.get("gap", 0),
        "round1_limit": Config.AGENT_EVIDENCE_GLOBAL,
        "chain_limit": (state.get("limits") or {}).get(
            POOL_CHAIN, Config.AGENT_CHAIN_EVIDENCE_BUDGET
        ),
        "gap_limit": Config.AGENT_GAP_EVIDENCE_BUDGET,
    }


def _timing_extra(
    started: float,
    plan_ms: int | None,
    usage: dict,
    round_two_ms: int | None = None,
    synthesis_ms: int | None = None,
    budget_exhausted: bool = False,
) -> dict:
    """trace 的模型、每步耗时、token 用量三块（设计文档 7.8 的 trace 要求）。"""
    return {
        "model": _model_info(),
        "timings": {
            "plan_ms": plan_ms,
            "round_two_ms": round_two_ms,
            "synthesis_ms": synthesis_ms,
            "total_ms": int((time.monotonic() - started) * 1000),
            "total_budget_s": Config.AGENT_TOTAL_BUDGET,
        },
        "usage": usage,
        "budget_exhausted": budget_exhausted,
    }


def _all_order_denied(results: list[dict]) -> bool:
    """全部子问题都被订单工具拒绝时为真：此时不得再调用 LLM。"""
    return bool(results) and all(
        item["source"] == SOURCE_ORDER
        and (item.get("order") or {}).get("status") == "denied"
        for item in results
    )


def build_trace(
    plan: dict,
    sub_results: list[dict],
    round_two_count: int = 0,
    single_hop: bool = False,
    fallback: bool = False,
    budgets: dict | None = None,
    fallback_reason: str | None = None,
    extra: dict | None = None,
) -> dict:
    """组装落库用 trace：短路/回退标记、轮次、预算池用量、逐子问题的答案与检索归因。

    extra 承载模型 id、每步耗时与 token 用量（见 `_timing_extra`）；
    trace 只供调试与评测，不在界面上展示明细（前端仅用子问题与覆盖状态）。
    """
    trace = {
        "single_hop": single_hop,
        "fallback": fallback,
        "fallback_reason": fallback_reason,
        "truncated_sub_questions": plan.get("truncated", 0),
        "rounds": 2 if round_two_count else 1,
        "round_two_sub_questions": round_two_count,
        "budgets": budgets or {},
        "sub_questions": [
            {
                "id": item["id"],
                "query": item["query"],
                "source": item["source"],
                "pool": item.get("pool"),
                "answer": item["answer"],
                "coverage": item["coverage"],
                "error": item["error"],
                "key_entities": item["key_entities"],
                "elapsed_ms": item.get("elapsed_ms"),
                "usage": item.get("usage") or {},
                "retrieval": item.get("retrieval") or [],
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
    if extra:
        trace.update(extra)
    return trace


def run_enhanced(
    question: str,
    user_role: str,
    history: list | None = None,
    trace_out: dict | None = None,
):
    """编排入口：产出 JSON 事件字符串（stage / token / done）。

    trace 通过 trace_out 回传——生成器无法 return 值，接口层传入一个字典，
    消费完事件流后即可拿到过程记录并随消息落库。

    整轮时间预算（`AGENT_TOTAL_BUDGET`）覆盖规划与两轮检索：规划后若已耗尽直接
    退回标准模式单跳；第二轮开始前耗尽则跳过补充检索（缺项显式写入结果）；
    合成始终执行——否则用户拿不到任何回答。
    """
    started = time.monotonic()
    deadline = started + Config.AGENT_TOTAL_BUDGET
    usage: dict = {"plan": {}, "sub_answers": [], "synthesis": {}}
    round_two_ms: int | None = None
    budget_exhausted = False

    def remaining() -> float:
        """整轮预算剩余秒数；每批子答案与第二轮开始前都要过这道闸。"""
        return deadline - time.monotonic()

    def write_trace(
        plan_obj,
        res,
        *,
        round_two_count=0,
        single_hop=False,
        fallback=False,
        reason=None,
        state=None,
        synthesis_ms=None,
    ) -> None:
        """把当前进度写进 trace_out（接口层的同一个 dict 会在消息落库时序列化）。"""
        if trace_out is None:
            return
        trace_out.update(
            build_trace(
                plan_obj,
                res,
                round_two_count=round_two_count,
                single_hop=single_hop,
                fallback=fallback,
                fallback_reason=reason,
                budgets=_budget_snapshot(state),
                extra=_timing_extra(
                    started,
                    plan_ms,
                    usage,
                    round_two_ms,
                    synthesis_ms,
                    budget_exhausted,
                ),
            )
        )

    def standard_fallback(prefix: str):
        """退回标准模式单跳；prefix 非空时先以 token 形式说明原因（故障不静默）。"""
        if prefix:
            yield _event({"token": prefix})
        for event in rag.answer_question(
            question, history=history, user_role=user_role, stream=True
        ):
            yield event

    search_tool = agent_tools.build_knowledge_search(
        user_role, k=Config.AGENT_RETRIEVE_K
    )
    order_tool = agent_tools.build_order_query(user_role)

    yield _event({"stage": "planning"})
    plan_started = time.monotonic()
    plan = plan_question(question, history)
    plan_ms = int((time.monotonic() - plan_started) * 1000)
    usage["plan"] = plan.get("usage") or {}

    if plan["fallback"]:
        # 规划失败：退回标准模式单跳，并显式告诉用户（故障不静默）
        write_trace(plan, [], single_hop=True, fallback=True, reason="planner_failed")
        yield from standard_fallback(PLANNER_FALLBACK_PREFIX)
        return

    if remaining() <= 0:
        # 规划就吃光了整轮预算：不再进入拆解循环
        budget_exhausted = True
        write_trace(plan, [], single_hop=True, fallback=True, reason="budget_exhausted")
        yield from standard_fallback(BUDGET_FALLBACK_PREFIX)
        return

    if not plan["needs_decomposition"]:
        # 单跳短路：界面不做任何提示，只在 trace 里留痕
        write_trace(plan, [], single_hop=True)
        yield from standard_fallback("")
        return

    if not plan["sub_questions"]:
        # 声称要拆解却没给出子问题（模型异常输出）：按规划失败处理，
        # 否则会带着空证据进入合成，等于给模型自由发挥的机会
        write_trace(plan, [], single_hop=True, fallback=True, reason="planner_failed")
        yield from standard_fallback(PLANNER_FALLBACK_PREFIX)
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
        timeout=min(Config.AGENT_SUB_TIMEOUT, max(1.0, remaining())),
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

    # 全部子问题被订单工具拒绝（越权）：不调用 LLM，直接给固定话术
    if not pending and _all_order_denied(results):
        write_trace(plan, results, reason="order_denied", state=state)
        yield _event({"token": order_qa.ORDER_FORBIDDEN_ANSWER})
        yield _event({"done": True, "sources": []})
        return

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
        round_two_started = time.monotonic()
        if remaining() <= 0:
            # 整轮预算已耗尽：补充检索不再执行，缺项按「证据缺失」显式写入结果
            budget_exhausted = True
            for item in round_two_plan["sub_questions"]:
                skipped = _base_result(item)
                skipped["error"] = "超出整轮时间预算，未执行补充检索"
                results.append(skipped)
                yield _event(
                    {
                        "stage": "sub_answer",
                        "sub_question_id": item["id"],
                        "answer": "",
                        "coverage": COVERAGE_MISSING,
                        "error": skipped["error"],
                        "follow_up": True,
                    }
                )
        else:
            extra_results: list[dict] = []
            # 链式池先跑：链式延伸是规划阶段就确定的义务，不该被机会主义的补查抢占；
            # 两个池额度独立、不互相借用（ADR 0010）
            for batch_pool in (POOL_CHAIN, POOL_GAP):
                batch = [
                    item
                    for item in round_two_plan["sub_questions"]
                    if item.get("pool") == batch_pool
                ]
                if not batch:
                    continue
                extra_results.extend(
                    answer_sub_questions(
                        {"sub_questions": batch},
                        question,
                        search_tool,
                        order_tool,
                        history,
                        state,
                        timeout=min(Config.AGENT_SUB_TIMEOUT, max(1.0, remaining())),
                    )
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
        round_two_ms = int((time.monotonic() - round_two_started) * 1000)

    usage["sub_answers"] = [
        {
            "id": item["id"],
            "elapsed_ms": item.get("elapsed_ms"),
            "usage": item.get("usage") or {},
        }
        for item in results
    ]

    # 全部子问题作答失败：退回标准模式单跳重试一次（设计文档 2.6）
    if results and all(item.get("error") for item in results):
        logger.warning("全部子问题作答失败，退回标准模式单跳")
        write_trace(
            plan,
            results,
            round_two_count=round_two_count,
            single_hop=True,
            fallback=True,
            reason="sub_answers_failed",
            state=state,
        )
        yield from standard_fallback(SUB_ANSWER_FALLBACK_PREFIX)
        return

    if _all_order_denied(results):
        write_trace(
            plan, results, round_two_count=round_two_count, reason="order_denied", state=state
        )
        yield _event({"token": order_qa.ORDER_FORBIDDEN_ANSWER})
        yield _event({"done": True, "sources": []})
        return

    write_trace(plan, results, round_two_count=round_two_count, state=state)

    yield _event({"stage": "synthesizing"})
    prompt = build_synthesis_prompt(question, results, history)
    synthesis_started = time.monotonic()
    synthesis_usage: dict = {}
    emitted = False
    try:
        for token in llm.stream(
            prompt,
            max_tokens=Config.AGENT_SYNTHESIS_MAX_TOKENS,
            usage=synthesis_usage,
        ):
            emitted = True
            yield _event({"token": token})
    except Exception:
        synthesis_ms = int((time.monotonic() - synthesis_started) * 1000)
        usage["synthesis"] = synthesis_usage
        if emitted:
            # 已经吐字，无法整段回退：交给接口层发 error 事件
            raise
        logger.exception("合成失败，退回标准模式单跳")
        write_trace(
            plan,
            results,
            round_two_count=round_two_count,
            fallback=True,
            reason="synthesis_failed",
            state=state,
            synthesis_ms=synthesis_ms,
        )
        yield from standard_fallback(SYNTHESIS_FALLBACK_PREFIX)
        return
    usage["synthesis"] = synthesis_usage
    if trace_out is not None:
        trace_out["timings"]["synthesis_ms"] = int(
            (time.monotonic() - synthesis_started) * 1000
        )
        trace_out["timings"]["total_ms"] = int((time.monotonic() - started) * 1000)
    yield _event({"done": True, "sources": collect_sources(results)})
