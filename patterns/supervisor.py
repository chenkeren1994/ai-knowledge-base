#!/usr/bin/env python3
"""Supervisor 监督模式：Worker 生成报告 → Supervisor 质量审核 → 循环改进。

Worker Agent 接收任务并输出 JSON 分析报告；
Supervisor Agent 对报告进行三维度质量评分（准确性 / 深度 / 格式）；
不通过则带反馈重做，最大重试轮数可配置。

用法::

    import asyncio
    from patterns.supervisor import supervisor

    async def main():
        result = await supervisor("分析 Transformer 架构的优缺点")
        print(result["output"])
        print(f"Final score: {result['final_score']}")

    asyncio.run(main())
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Worker Agent
# ---------------------------------------------------------------------------

_WORKER_SYSTEM = """你是一个技术分析专家。根据用户的任务描述，生成一份 JSON 格式的分析报告。

报告格式：
{
  "analysis": "详细分析（200-500字）",
  "key_points": ["要点1", "要点2", "要点3"],
  "conclusion": "总结（1-2句）"
}

请严格以 JSON 格式输出，不要包含 markdown 代码块标记。"""


async def _worker(task: str, feedback: str = "") -> dict[str, Any]:
    """Worker Agent：接收任务并生成 JSON 分析报告。

    Args:
        task: 任务描述。
        feedback: 上轮 Supervisor 的反馈（首次调用为空）。

    Returns:
        解析后的分析报告字典。
    """
    from workflows.model_client import chat

    prompt = task
    if feedback:
        prompt = (
            f"原始任务：{task}\n\n"
            f"上一轮反馈：{feedback}\n\n"
            f"请根据反馈改进分析报告，严格输出 JSON 格式。"
        )

    text, usage = await chat(
        prompt=prompt,
        system_prompt=_WORKER_SYSTEM,
        temperature=0.5,
        max_tokens=2048,
    )
    logger.info("Worker: %d tokens used", usage.total_tokens)
    return _extract_json(text)


# ---------------------------------------------------------------------------
# Supervisor Agent
# ---------------------------------------------------------------------------

_SUPERVISOR_SYSTEM = """你是一个质量审核专家。对技术分析报告进行三维度评分：

1. 准确性（1-10）：报告内容是否准确、事实是否正确
2. 深度（1-10）：分析是否有深度、是否涵盖关键要点
3. 格式（1-10）：报告结构是否清晰、格式是否规范

综合评分 = round((准确性 + 深度 + 格式) / 3)

输出 JSON：
{
  "passed": true/false,
  "score": 8,
  "accuracy": 8,
  "depth": 8,
  "format": 8,
  "feedback": "具体改进建议"
}

pass 条件：综合评分 >= 7。feedback 中给出具体、可操作的改进建议。
请严格以 JSON 格式输出，不要包含 markdown 代码块标记。"""


async def _supervisor(report: dict[str, Any]) -> dict[str, Any]:
    """Supervisor Agent：对 Worker 报告进行质量审核。

    Args:
        report: Worker 生成的 JSON 分析报告。

    Returns:
        审核结果，包含 passed / score / feedback 等字段。
    """
    from workflows.model_client import chat

    report_text = json.dumps(report, ensure_ascii=False, indent=2)

    text, usage = await chat(
        prompt=f"请审核以下分析报告：\n\n{report_text}",
        system_prompt=_SUPERVISOR_SYSTEM,
        temperature=0.3,
        max_tokens=1024,
    )
    logger.info("Supervisor: %d tokens used", usage.total_tokens)

    result = _extract_json(text)
    result.setdefault("passed", False)
    result.setdefault("score", 0)
    result.setdefault("feedback", "")
    return result


# ---------------------------------------------------------------------------
# JSON 提取工具
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict[str, Any]:
    """从 LLM 输出文本中提取 JSON 对象。

    Args:
        text: LLM 原始输出文本。

    Returns:
        解析后的字典。

    Raises:
        json.JSONDecodeError: JSON 解析失败。
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines).strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(text)


# ---------------------------------------------------------------------------
# Supervisor 主函数
# ---------------------------------------------------------------------------

async def supervisor(
    task: str,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Supervisor 监督模式主入口。

    Worker 生成分析报告，Supervisor 进行质量审核，
    不通过则带反馈重做，直到通过或达到最大重试次数。

    Args:
        task: 任务描述。
        max_retries: 最大重试轮数，默认 3。

    Returns:
        包含以下字段的字典：
        - output: Worker 最终的分析报告（dict）
        - attempts: 总尝试次数
        - final_score: 最终综合评分
        - warning: 超过最大重试时的警告信息（可选）
    """
    if not task or not task.strip():
        raise ValueError("task 不能为空")

    logger.info("=" * 50)
    logger.info("Supervisor pattern started, task: %s", task[:80])

    output: dict[str, Any] = {}
    attempts = 0
    final_score = 0
    warning: str | None = None
    feedback: str = ""

    for attempt in range(1, max_retries + 2):
        attempts = attempt
        logger.info("--- Attempt %d ---", attempt)

        # Step 1: Worker 生成报告
        try:
            output = await _worker(task, feedback)
            logger.info("Worker output generated successfully")
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Worker failed on attempt %d: %s", attempt, exc)
            feedback = f"JSON 解析失败：{exc}。请确保输出严格 JSON 格式。"
            continue

        # Step 2: Supervisor 审核
        try:
            review = await _supervisor(output)
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Supervisor failed on attempt %d: %s", attempt, exc)
            feedback = f"审核过程出错：{exc}。请重新生成报告。"
            continue

        final_score = review.get("score", 0)
        passed = review.get("passed", False)
        feedback = review.get("feedback", "")

        logger.info(
            "Attempt %d: score=%d, passed=%s",
            attempt, final_score, passed,
        )

        if passed:
            logger.info("Passed! Returning result.")
            break

        if attempt >= max_retries + 1:
            warning = (
                f"超过最大重试次数（{max_retries}），强制返回。"
                f"最终评分：{final_score}/10"
            )
            logger.warning(warning)
            break

    return {
        "output": output,
        "attempts": attempts,
        "final_score": final_score,
        "warning": warning,
    }


# ---------------------------------------------------------------------------
# 测试入口
# ---------------------------------------------------------------------------

async def _test_supervisor() -> None:
    """测试 Supervisor 监督模式。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    task = "分析 RAG（检索增强生成）技术的工作原理、关键组件和常见挑战。请用中文回答。"

    print(f"\n{'─' * 60}")
    print("Supervisor 监督模式测试")
    print(f"Task: {task}")
    print(f"{'─' * 60}")

    result = await supervisor(task, max_retries=3)

    print(f"\n{'─' * 60}")
    print(f"Final Score: {result['final_score']}/10")
    print(f"Attempts: {result['attempts']}")
    if result.get("warning"):
        print(f"Warning: {result['warning']}")
    print(f"\nOutput:")
    print(json.dumps(result["output"], ensure_ascii=False, indent=2))
    print(f"\n{'─' * 60}")
    print("测试完成")


if __name__ == "__main__":
    asyncio.run(_test_supervisor())
