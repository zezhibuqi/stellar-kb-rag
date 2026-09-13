"""增强模式编排（设计文档 7.8）。

本模块当前实现**规划步**：一次 LLM 调用产出「是否需要拆解 + 意图 + 结构化过滤条件
+ 子问题列表」，解析失败重试一次，仍失败则打回退标记，由上层退回标准模式单跳。
子答案并行、有界第二轮、逐项合成在后续提交中补齐。
"""

import logging

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
