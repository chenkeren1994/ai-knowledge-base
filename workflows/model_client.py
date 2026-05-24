#!/usr/bin/env python3
"""简化的 LLM 客户端封装。

提供 ``chat()`` 和 ``chat_json()`` 两个高层函数，
内部委托给 ``pipeline/model_client.py``。

用法::

    import asyncio
    from workflows.model_client import chat, chat_json

    async def main():
        text, usage = await chat("用一句话介绍 Python")
        print(text)
        print(f"tokens: {usage.total_tokens}")

        result = await chat_json('{"name": "RAG"} 输出 JSON')
        print(result["name"])

    asyncio.run(main())
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# 确保可以 import pipeline 包
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from pipeline.model_client import (  # noqa: E402
    Usage,
    chat_with_retry,
    get_provider,
)


async def chat(
    prompt: str,
    system_prompt: str = "",
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> tuple[str, Usage]:
    """发送对话请求，返回 ``(文本, 用量)`` 元组。

    Args:
        prompt: 用户输入的提示词。
        system_prompt: 可选的系统提示。
        model: 模型名称，为空则使用默认模型。
        temperature: 采样温度。
        max_tokens: 最大输出 Token 数。

    Returns:
        ``(content_text, Usage)`` 元组，其中 ``Usage`` 包含
        ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens``。
    """
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    response = await chat_with_retry(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.content, response.usage


async def chat_json(
    prompt: str,
    system_prompt: str = "",
    model: str = "",
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> tuple[dict[str, Any], Usage]:
    """发送对话请求并将响应解析为 JSON。

    系统提示中会自动追加 JSON 输出格式要求。

    Args:
        prompt: 用户输入的提示词。
        system_prompt: 可选的系统提示。
        model: 模型名称，为空则使用默认模型。
        temperature: 采样温度（建议较低以保证 JSON 稳定性）。
        max_tokens: 最大输出 Token 数。

    Returns:
        ``(parsed_json, Usage)`` 元组，其中 ``Usage`` 包含 Token 统计。

    Raises:
        json.JSONDecodeError: 如果 LLM 输出不是合法 JSON。
    """
    json_hint = "\n请严格以 JSON 格式输出，不要包含 markdown 代码块标记。"
    full_system = system_prompt + json_hint if system_prompt else json_hint.lstrip()

    text, usage = await chat(
        prompt=prompt,
        system_prompt=full_system,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines).strip()

    return json.loads(text), usage


def accumulate_usage(cost_tracker: dict, usage: Usage, model: str = "") -> dict:
    """累加 Token 统计到 cost_tracker 字典。

    Args:
        cost_tracker: KBState 中的 ``cost_tracker`` 字典，会被原地修改。
        usage: 单次 LLM 调用的用量。
        model: 使用的模型名称，用于去重记录模型列表。

    Returns:
        更新后的 cost_tracker 字典（与传入的是同一对象）。
    """
    cost_tracker["total_tokens"] = cost_tracker.get("total_tokens", 0) + usage.total_tokens
    cost_tracker["total_cost_cny"] = cost_tracker.get("total_cost_cny", 0.0)
    cost_tracker["records"] = cost_tracker.get("records", 0) + 1
    models: list[str] = cost_tracker.setdefault("models", [])
    if model and model not in models:
        models.append(model)
    return cost_tracker
