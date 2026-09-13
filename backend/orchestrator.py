"""增强模式编排（设计文档 7.8）。

本模块当前实现**规划步**：一次 LLM 调用产出「是否需要拆解 + 意图 + 结构化过滤条件
+ 子问题列表」，解析失败重试一次，仍失败则打回退标记，由上层退回标准模式单跳。
子答案并行、有界第二轮、逐项合成在后续提交中补齐。
"""

import logging
from concurrent.futures import ThreadPoolExecutor, wait

import chroma_store
import llm
import order_qa
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

# 单次子答案调用的 token 预算（要在 JSON 里放下答案、覆盖状态与中间实体）
SUB_ANSWER_MAX_TOKENS = 800

_PLAN_EXAMPLES = """示例1（并列型，同一实体的多个属性）：
用户：2025 年动力电池系统的营收 / 占比 / 毛利率 / 销量？
输出：{"needs_decomposition": true, "intent": "knowledge", "filters": {}, "aggregation": null,
      "sub_questions": [
        {"id": 1, "query": "2025 动力电池系统 营收", "source": "knowledge"},
        {"id": 2, "query": "2025 动力电池系统 占比", "source": "knowledge"},
        {"id": 3, "query": "2025 动力电池系统 毛利率", "source": "knowledge"},
        {"id": 4, "query": "2025 动力电池系统 销量", "source": "knowledge"}]}

示例2（链式型，第二跳的实体要等第一跳结果才知道）：
用户：为 SC-500 工商业储能一体柜供电的那款电芯，它的单体质量能量密度和 25℃ 循环寿命分别是多少？
输出：{"needs_decomposition": true, "intent": "knowledge", "filters": {}, "aggregation": null,
      "sub_questions": [
        {"id": 1, "query": "SC-500 工商业储能一体柜 配套 电芯 型号", "source": "knowledge"}]}

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
        "4. 后续子问题依赖前面结果的链式问题，只输出当前能确定的第一步，"
        "后续步骤由系统在拿到中间结果后再查。\n"
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
    return {
        "id": index,
        "query": query,
        "source": source,
        "filters": _clean_filters(raw.get("filters")),
        "aggregation": _clean_aggregation(raw.get("aggregation")),
    }


def _normalize(data: dict) -> dict:
    intent = data.get("intent")
    if intent not in INTENTS:
        raise ValueError(f"非法 intent：{intent}")

    raw_sub_questions = data.get("sub_questions") or []
    if not isinstance(raw_sub_questions, list):
        raise ValueError("sub_questions 必须为数组")

    sub_questions: list[dict] = []
    for raw in raw_sub_questions:
        item = _sanitize_sub_question(raw, len(sub_questions) + 1)
        if item is not None:
            sub_questions.append(item)

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
            return _normalize(llm.invoke_json(prompt))
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


def collect_evidence(sub_questions: list[dict], search_tool, order_tool) -> dict:
    """按每子问题配额与全局上限裁决证据，跨子问题按证据单元去重。"""
    per_sub: dict[int, dict] = {}
    seen: set = set()
    total = 0
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

        result = search_tool(sub_question["query"])
        items: list[dict] = []
        for item in result.get("items") or []:
            if len(items) >= per_limit or total >= global_limit:
                break
            key = chroma_store.evidence_unit_key(
                item.get("doc_id"),
                item.get("parent_start_line"),
                item.get("parent_end_line"),
            )
            if key in seen:
                continue
            seen.add(key)
            total += 1
            items.append(item)
        per_sub[sub_question["id"]] = {
            "kind": SOURCE_KNOWLEDGE,
            "result": result,
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
) -> list[dict]:
    """并行取得每个子问题的子答案；单个子问题失败不影响其他子问题。"""
    sub_questions = plan.get("sub_questions") or []
    if not sub_questions:
        return []

    evidence_map = collect_evidence(sub_questions, search_tool, order_tool)
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
