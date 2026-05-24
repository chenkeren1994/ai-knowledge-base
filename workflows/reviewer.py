#!/usr/bin/env python3
"""Reviewer 审核节点。

对 ``state["analyses"]`` 中的分析结果进行五维度 LLM 评分，
加权总分 >= 7.0 为通过，否则退回整理阶段修正。

用法::

    from workflows.reviewer import review_node

    result = await review_node(state)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from workflows.model_client import chat_json, accumulate_usage
from workflows.state import KBState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_MAX_REVIEW_ITEMS = 5
_PASS_THRESHOLD = float(os.getenv("REVIEW_PASS_THRESHOLD", "7.0"))

_WEIGHTS = {
    "summary_quality": 0.25,
    "technical_depth": 0.25,
    "relevance": 0.20,
    "originality": 0.15,
    "formatting": 0.15,
}

_REVIEW_SYSTEM_PROMPT = """你是一个技术内容审核专家。对 AI/LLM/Agent 领域的技术条目进行五维度质量评分。

每个维度评分范围 1-10 分，请严格按以下标准：

1. **summary_quality**（摘要质量，25%）：摘要是否简洁准确、突出核心技术点、无空洞词
2. **technical_depth**（技术深度，25%）：是否体现技术深度、有具体实现细节或创新点
3. **relevance**（相关性，20%）：与 AI/LLM/Agent 核心领域的相关程度
4. **originality**（原创性，15%）：内容是否有独特视角或创新，非纯搬运或重复信息
5. **formatting**（格式规范，15%）：highlights 是否精炼、tags 是否从标准标签库选择、评分是否合理

请对每条条目输出五维度评分，严格以 JSON 数组格式输出：
[
  {
    "title": "条目标题",
    "scores": {
      "summary_quality": 8,
      "technical_depth": 7,
      "relevance": 9,
      "originality": 6,
      "formatting": 8
    },
    "feedback": "具体改进建议（如某维度偏低须说明原因）"
  }
]

注意：
- 每条必须包含所有 5 个维度评分
- 评分必须为 1-10 的整数
- feedback 须具体指出问题，不要泛泛而谈"""


def _calculate_weighted_total(scores: dict[str, int]) -> float:
    """计算加权总分（不信任模型算术）。

    Args:
        scores: 五维度评分字典，值范围 1-10。

    Returns:
        加权总分，范围 1.0-10.0。
    """
    total = 0.0
    for dim, weight in _WEIGHTS.items():
        score = scores.get(dim, 5)
        score = max(1, min(10, score))
        total += score * weight
    return round(total, 2)


def _clamp_scores(scores: dict) -> dict[str, int]:
    """校验并修正评分值，确保在 1-10 范围。

    Args:
        scores: 原始评分字典。

    Returns:
        修正后的评分字典，所有值均为 1-10 的整数。
    """
    clamped: dict[str, int] = {}
    for dim in _WEIGHTS:
        val = scores.get(dim, 5)
        try:
            val = int(val)
        except (TypeError, ValueError):
            val = 5
        clamped[dim] = max(1, min(10, val))
    return clamped


async def review_node(state: KBState) -> dict:
    """审核节点：对 analyses 进行五维度 LLM 评分。

    只审核前 5 条 analyses 以控制 token 消耗。
    加权总分 >= 7.0 为通过，否则退回修正。
    LLM 调用失败时自动通过，不阻塞流程。

    Args:
        state: 工作流共享状态。

    Returns:
        包含 ``review_passed`` / ``review_feedback`` / ``iteration`` / ``cost_tracker`` 的部分状态更新。
    """
    logger.info("[ReviewNode] 开始审核")

    iteration = state.get("iteration", 0)
    analyses = state.get("analyses", [])
    cost_tracker = state.get("cost_tracker", {}).copy()

    if not analyses:
        logger.warning("[ReviewNode] 无分析结果，视为通过")
        return {
            "review_passed": True,
            "review_feedback": "",
            "iteration": iteration + 1,
            "cost_tracker": cost_tracker,
        }

    review_items = analyses[:_MAX_REVIEW_ITEMS]
    logger.info("[ReviewNode] 审核前 %d 条（共 %d 条）", len(review_items), len(analyses))

    content_items: list[str] = []
    for a in review_items:
        content_items.append(
            f"标题: {a.get('title', '')}\n"
            f"摘要: {a.get('summary', '')}\n"
            f"亮点: {', '.join(a.get('highlights', []))}\n"
            f"相关性评分: {a.get('relevance', 5)}\n"
            f"标签: {', '.join(a.get('tags', []))}"
        )

    content = "请审核以下条目：\n\n" + "\n\n".join(content_items)

    try:
        result, usage = await chat_json(
            prompt=content,
            system_prompt=_REVIEW_SYSTEM_PROMPT,
            temperature=0.1,
            max_tokens=2048,
        )
    except Exception as exc:
        logger.error("[ReviewNode] LLM 调用失败: %s，自动通过", exc)
        return {
            "review_passed": True,
            "review_feedback": "",
            "iteration": iteration + 1,
            "cost_tracker": cost_tracker,
        }

    accumulate_usage(cost_tracker, usage)

    if not isinstance(result, list):
        logger.warning("[ReviewNode] LLM 返回非数组格式，自动通过")
        return {
            "review_passed": True,
            "review_feedback": "",
            "iteration": iteration + 1,
            "cost_tracker": cost_tracker,
        }

    all_weighted_totals: list[float] = []
    all_feedbacks: list[str] = []

    for item_result in result:
        title = item_result.get("title", "unknown")
        scores_raw = item_result.get("scores", {})
        feedback = item_result.get("feedback", "")

        scores = _clamp_scores(scores_raw)
        weighted_total = _calculate_weighted_total(scores)
        all_weighted_totals.append(weighted_total)

        logger.info(
            "[ReviewNode] %s: summary=%d, depth=%d, relevance=%d, "
            "originality=%d, format=%d → 加权总分=%.2f",
            title,
            scores["summary_quality"],
            scores["technical_depth"],
            scores["relevance"],
            scores["originality"],
            scores["formatting"],
            weighted_total,
        )

        if weighted_total < _PASS_THRESHOLD and feedback:
            all_feedbacks.append(f"[{title}] {feedback}")

    overall_passed = all(t >= _PASS_THRESHOLD for t in all_weighted_totals) if all_weighted_totals else True
    overall_feedback = "\n".join(all_feedbacks) if all_feedbacks else ""

    logger.info(
        "[ReviewNode] 审核结果: passed=%s, 最低分=%.2f, 最高分=%.2f",
        overall_passed,
        min(all_weighted_totals) if all_weighted_totals else 0,
        max(all_weighted_totals) if all_weighted_totals else 0,
    )
    if overall_feedback:
        logger.info("[ReviewNode] 反馈: %s", overall_feedback)

    return {
        "review_passed": overall_passed,
        "review_feedback": overall_feedback,
        "iteration": iteration + 1,
        "cost_tracker": cost_tracker,
    }
