#!/usr/bin/env python3
"""Planner 规划节点。

根据目标采集量自动选择策略档位，输出 ``{"plan": {...}}``
供下游 collector / organizer / reviewer 读取。

三档策略：lite（轻量）、standard（标准）、full（全量）。

用法::

    from workflows.planner import plan_strategy, planner_node

    # 直接调用策略生成
    plan = plan_strategy(target_count=15)

    # LangGraph 节点包装
    result = await planner_node(state)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from workflows.state import KBState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 策略档位定义
# ---------------------------------------------------------------------------

Strategies: dict[str, dict] = {
    "lite": {
        "tier": "lite",
        "per_source_limit": 5,
        "relevance_threshold": 0.7,
        "max_iterations": 1,
    },
    "standard": {
        "tier": "standard",
        "per_source_limit": 10,
        "relevance_threshold": 0.5,
        "max_iterations": 2,
    },
    "full": {
        "tier": "full",
        "per_source_limit": 20,
        "relevance_threshold": 0.4,
        "max_iterations": 3,
    },
}

# ---------------------------------------------------------------------------
# 档位选择逻辑
# ---------------------------------------------------------------------------

_TIER_RANGES: list[tuple[str, int | None, int | None]] = [
    ("lite", None, 9),
    ("standard", 10, 19),
    ("full", 20, None),
]


def _resolve_tier(target_count: int) -> str:
    """根据目标采集量解析策略档位。

    Args:
        target_count: 目标采集条目数。

    Returns:
        档位标识：``lite`` / ``standard`` / ``full``。

    Raises:
        ValueError: target_count 非法（<= 0）。
    """
    if target_count <= 0:
        raise ValueError(f"target_count 必须为正整数，收到 {target_count}")

    for tier, lo, hi in _TIER_RANGES:
        if lo is not None and target_count < lo:
            continue
        if hi is not None and target_count > hi:
            continue
        return tier

    return "full"


_RATIONALE_TEMPLATES: dict[str, str] = {
    "lite": (
        "目标采集量 < 10，选择 lite 档位：每条源最多 {per_source_limit} 条，"
        "相关性阈值 {relevance_threshold}，最多 {max_iterations} 轮审核。"
        "适合快速试跑或低流量日期，优先保证条目质量而非数量。"
    ),
    "standard": (
        "目标采集量在 10-19 之间，选择 standard 档位：每条源最多 {per_source_limit} 条，"
        "相关性阈值 {relevance_threshold}，最多 {max_iterations} 轮审核。"
        "适合日常采集，在数量与质量之间取得平衡。"
    ),
    "full": (
        "目标采集量 >= 20，选择 full 档位：每条源最多 {per_source_limit} 条，"
        "相关性阈值 {relevance_threshold}，最多 {max_iterations} 轮审核。"
        "适合全量采集场景，宽进严出，通过多轮审核保证最终输出质量。"
    ),
}

# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def plan_strategy(target_count: int | None = None) -> dict[str, Any]:
    """根据目标采集量返回策略 dict。

    策略包含以下字段：
    - ``tier`` (str): 档位标识
    - ``per_source_limit`` (int): 每源采集上限
    - ``relevance_threshold`` (float): 相关性过滤阈值
    - ``max_iterations`` (int): 最大审核轮数
    - ``target_count`` (int): 实际使用的目标值
    - ``rationale`` (str): 选档理由

    Args:
        target_count: 目标采集条目数。默认从环境变量
            ``PLANNER_TARGET_COUNT`` 读取（默认 10）。

    Returns:
        策略配置 dict。
    """
    if target_count is None:
        target_count = int(os.getenv("PLANNER_TARGET_COUNT", "10"))

    tier = _resolve_tier(target_count)
    strategy = dict(Strategies[tier])
    strategy["target_count"] = target_count
    strategy["rationale"] = _RATIONALE_TEMPLATES[tier].format(
        per_source_limit=strategy["per_source_limit"],
        relevance_threshold=strategy["relevance_threshold"],
        max_iterations=strategy["max_iterations"],
    )

    logger.info(
        "[Planner] target_count=%d → tier=%s (limit=%d, threshold=%.1f, max_iter=%d)",
        target_count,
        tier,
        strategy["per_source_limit"],
        strategy["relevance_threshold"],
        strategy["max_iterations"],
    )

    return strategy


async def planner_node(state: KBState) -> dict:
    """LangGraph 规划节点：生成执行计划并写入 state["plan"]。

    从 ``state`` 中读取 ``target_count`` 配置（如有），
    调用 ``plan_strategy`` 生成策略 dict，以 ``{"plan": ...}`` 格式返回。

    Args:
        state: 工作流共享状态。可通过 ``state["target_count"]`` 传入目标值。

    Returns:
        包含 ``plan`` 的部分状态更新。
    """
    logger.info("[PlannerNode] 开始规划")

    target_count = state.get("target_count") or None  # type: ignore[arg-type]
    plan = plan_strategy(target_count)
    return {"plan": plan}
