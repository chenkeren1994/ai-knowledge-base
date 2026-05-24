#!/usr/bin/env python3
"""Reviser 修订节点。

根据审核反馈对 ``state["analyses"]`` 进行定向修正，
输出改进后的分析结果列表。

用法::

    from workflows.reviser import revise_node

    result = await revise_node(state)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from workflows.model_client import chat_json, accumulate_usage
from workflows.state import KBState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_REVISE_SYSTEM_PROMPT = """你是一个技术内容编辑。根据审核反馈意见，修正以下知识条目的分析结果。

审核反馈：
{feedback}

请针对反馈逐条修正，严格以 JSON 数组格式输出修正后的完整条目列表：
[
  {{
    "title": "条目标题（保持不变）",
    "summary": "修正后的中文摘要（1-3 句）",
    "highlights": ["修正后的亮点1", "修正后的亮点2"],
    "relevance": <修正后的评分 1-10>,
    "tags": ["修正后的标签1", "修正后的标签2"]
  }}
]

修正原则：
1. 严格按照审核反馈的具体建议修改，不要过度调整
2. 如果反馈提到摘要问题（空洞词、信息量低），重写摘要使其更具体有技术深度
3. 如果反馈提到标签缺失或错误，根据条目内容补充或更正标签
4. 如果反馈提到评分不合理，根据评分标准重新评估
5. 保持 title 字段不变，仅修改分析相关字段
6. 输出条目数量必须与输入一致"""


def _build_revise_prompt(analyses: list[dict[str, Any]], feedback: str) -> str:
    """构建修订提示词。

    Args:
        analyses: 待修正的分析结果列表。
        feedback: 审核反馈意见。

    Returns:
        完整的用户提示词。
    """
    items_text: list[str] = []
    for i, a in enumerate(analyses, 1):
        items_text.append(
            f"条目 {i}:\n"
            f"  标题: {a.get('title', '')}\n"
            f"  摘要: {a.get('summary', '')}\n"
            f"  亮点: {json.dumps(a.get('highlights', []), ensure_ascii=False)}\n"
            f"  相关性: {a.get('relevance', 5)}\n"
            f"  标签: {json.dumps(a.get('tags', []), ensure_ascii=False)}"
        )

    return (
        f"审核反馈：\n{feedback}\n\n"
        f"请根据以上反馈修正以下条目：\n\n"
        + "\n\n".join(items_text)
    )


async def revise_node(state: KBState) -> dict:
    """修订节点：根据审核反馈修正 analyses。

    Args:
        state: 工作流共享状态。

    Returns:
        包含 ``analyses`` 和 ``cost_tracker`` 的部分状态更新。
        无反馈或无分析结果时返回空 dict。
    """
    logger.info("[ReviseNode] 开始修订")

    analyses = state.get("analyses", [])
    feedback = state.get("review_feedback", "")
    cost_tracker = state.get("cost_tracker", {}).copy()

    if not analyses or not feedback:
        logger.info("[ReviseNode] 无分析结果或无反馈，跳过修订")
        return {}

    logger.info("[ReviseNode] 修订 %d 条，反馈: %s", len(analyses), feedback[:100])

    prompt = _build_revise_prompt(analyses, feedback)

    try:
        result, usage = await chat_json(
            prompt=prompt,
            system_prompt=_REVISE_SYSTEM_PROMPT.format(feedback=feedback),
            temperature=0.4,
            max_tokens=2048,
        )
    except Exception as exc:
        logger.error("[ReviseNode] LLM 调用失败: %s，保留原分析结果", exc)
        return {}

    accumulate_usage(cost_tracker, usage)

    if not isinstance(result, list) or len(result) != len(analyses):
        logger.warning(
            "[ReviseNode] LLM 返回格式异常（期望 %d 条数组，实际 %s），保留原结果",
            len(analyses),
            type(result).__name__,
        )
        return {}

    improved: list[dict[str, Any]] = []
    for original, revised in zip(analyses, result):
        merged = {**original}
        merged["summary"] = revised.get("summary", original.get("summary", ""))
        merged["highlights"] = revised.get("highlights", original.get("highlights", []))
        merged["relevance"] = revised.get("relevance", original.get("relevance", 5))
        merged["tags"] = revised.get("tags", original.get("tags", []))
        improved.append(merged)

    logger.info("[ReviseNode] 修订完成: %d 条", len(improved))

    return {
        "analyses": improved,
        "cost_tracker": cost_tracker,
    }
