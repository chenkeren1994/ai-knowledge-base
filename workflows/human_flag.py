#!/usr/bin/env python3
"""HumanFlag 人工标记节点。

当审核循环超过 ``max_iterations`` 仍未通过时触发，
将问题条目写入独立目录 ``knowledge/flagged/``，
不污染主知识库，等待人工判断。

用法::

    from workflows.human_flag import human_flag_node

    result = await human_flag_node(state)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workflows.state import KBState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_FLAGGED_DIR = _PROJECT_ROOT / "knowledge" / "flagged"


def _build_flagged_filename(iteration: int) -> str:
    """生成标记文件名。

    Args:
        iteration: 当前迭代次数。

    Returns:
        形如 ``flagged-20260524-iter3.json`` 的文件名。
    """
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"flagged-{date_part}-iter{iteration}.json"


async def human_flag_node(state: KBState) -> dict:
    """人工标记节点：将未通过审核的条目写入独立目录。

    当审核循环超过上限仍未通过时调用，
    将问题条目保存到 ``knowledge/flagged/`` 目录，
    附带审核反馈和迭代信息供人工参考。

    Args:
        state: 工作流共享状态。

    Returns:
        包含 ``review_passed``（强制 True 以终止循环）和 ``flagged_path`` 的状态更新。
    """
    logger.info("[HumanFlagNode] 开始人工标记")

    analyses = state.get("analyses", [])
    iteration = state.get("iteration", 0)
    feedback = state.get("review_feedback", "")

    _FLAGGED_DIR.mkdir(parents=True, exist_ok=True)

    flagged_payload = {
        "flagged_at": datetime.now(timezone.utc).isoformat(),
        "iteration": iteration,
        "review_feedback": feedback,
        "reason": f"审核循环 {iteration} 次未通过，需人工判断",
        "items": [
            {
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "summary": a.get("summary", ""),
                "highlights": a.get("highlights", []),
                "relevance": a.get("relevance", 5),
                "tags": a.get("tags", []),
            }
            for a in analyses
        ],
    }

    if not analyses:
        logger.warning("[HumanFlagNode] 无分析结果，但仍生成标记文件")
        flagged_payload["items"] = []
        flagged_payload["note"] = "analyses 字段为空，可能因状态传递问题或前置节点跳过"

    filename = _build_flagged_filename(iteration)
    filepath = _FLAGGED_DIR / filename

    try:
        filepath.write_text(
            json.dumps(flagged_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("[HumanFlagNode] 已标记 %d 条到 %s", len(analyses), filepath)
    except OSError as exc:
        logger.error("[HumanFlagNode] 写入失败: %s — %s", filepath, exc)

    return {
        "review_passed": True,
        "flagged_path": str(filepath),
    }
