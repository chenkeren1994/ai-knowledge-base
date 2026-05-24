#!/usr/bin/env python3
"""LangGraph 工作流编排。

组装规划驱动的审核循环流水线：

.. code-block::

    planner → collect → analyze → organize → review ── passed ──→ save → END
                                      │            │
                                      │            ├── not passed, iter < max_iter ──→ revise ──┐
                                      │            │                                        │
                                      │            └── not passed, iter >= max_iter ──→ human_flag → END
                                      └───────────────────────────────────────────────────────┘
                                                     (修订后重新组织)

用法::

    python workflows/graph.py
"""

from __future__ import annotations

import asyncio
import logging

from langgraph.graph import END, StateGraph

from workflows.human_flag import human_flag_node
from workflows.nodes import (
    analyze_node,
    collect_node,
    organize_node,
    save_node,
)
from workflows.planner import planner_node
from workflows.reviser import revise_node
from workflows.reviewer import review_node
from workflows.state import KBState

logger = logging.getLogger(__name__)


def route_after_review(state: KBState) -> str:
    """审核后的 3 路条件路由。

    Args:
        state: 工作流共享状态。

    Returns:
        - ``"save"``: 审核通过，进入保存节点
        - ``"revise"``: 审核不通过且 iteration < max_iterations，退回修订节点
        - ``"human_flag"``: 审核不通过且 iteration >= max_iterations，转人工标记
    """
    passed = state.get("review_passed", False)
    iteration = state.get("iteration", 0)
    plan = state.get("plan", {}) or {}
    max_iterations = int(plan.get("max_iterations", 3))

    if passed:
        return "save"
    if iteration < max_iterations:
        return "revise"
    return "human_flag"


def build_graph() -> StateGraph:
    """构建并编译 LangGraph 工作流。

    Returns:
        编译后的 StateGraph 实例（可直接调用 ``.astream()`` / ``.ainvoke()``）。
    """
    graph = StateGraph(KBState)

    # 注册节点
    graph.add_node("planner", planner_node)
    graph.add_node("collect", collect_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("organize", organize_node)
    graph.add_node("review", review_node)
    graph.add_node("revise", revise_node)
    graph.add_node("human_flag", human_flag_node)
    graph.add_node("save", save_node)

    # 规划 → 采集
    graph.add_edge("planner", "collect")
    # 线性链
    graph.add_edge("collect", "analyze")
    graph.add_edge("analyze", "organize")
    graph.add_edge("organize", "review")

    # 审核 3 路分支
    graph.add_conditional_edges(
        "review",
        route_after_review,
        {
            "save": "save",
            "revise": "revise",
            "human_flag": "human_flag",
        },
    )

    # 修订 → 审核（形成循环）
    graph.add_edge("revise", "review")

    # 人工标记 → 终止
    graph.add_edge("human_flag", END)

    # 保存 → 终止
    graph.add_edge("save", END)

    graph.set_entry_point("planner")

    return graph.compile()


# ---------------------------------------------------------------------------
# 流式执行入口
# ---------------------------------------------------------------------------


async def _main() -> None:
    """流式执行工作流并打印每个节点的关键输出。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    app = build_graph()

    initial_state: KBState = {
        "plan": {},
        "sources": [],
        "analyses": [],
        "articles": [],
        "review_feedback": "",
        "review_passed": False,
        "iteration": 0,
        "cost_tracker": {},
        "needs_human_review": False,
        "flagged_path": "",
    }

    logger.info("=" * 50)
    logger.info("LangGraph 工作流启动")
    logger.info("=" * 50)

    async for event in app.astream(
        initial_state,
        stream_mode="updates",
    ):
        for node_name, node_output in event.items():
            print(f"\n{'─' * 50}")
            print(f"【{node_name}】")

            if node_name == "collect":
                sources = node_output.get("sources", [])
                for s in sources:
                    print(f"  来源: {s.get('source')}, 采集数: {s.get('count')}, 状态: {s.get('status')}")

            elif node_name == "analyze":
                analyses = node_output.get("analyses", [])
                print(f"  分析条目数: {len(analyses)}")
                for a in analyses[:3]:
                    print(f"    - {a.get('title')}: relevance={a.get('relevance')}, tags={a.get('tags')}")
                if len(analyses) > 3:
                    print(f"    ... 及 {len(analyses) - 3} 条")

            elif node_name == "organize":
                articles = node_output.get("articles", [])
                print(f"  整理条目数: {len(articles)}")
                for a in articles[:3]:
                    print(f"    - [{a.get('id')}] {a.get('title')}: relevance={a.get('relevance')}, status={a.get('status')}")
                if len(articles) > 3:
                    print(f"    ... 及 {len(articles) - 3} 条")

            elif node_name == "review":
                passed = node_output.get("review_passed", False)
                feedback = node_output.get("review_feedback", "")
                iteration = node_output.get("iteration", 0)
                print(f"  审核通过: {passed}")
                print(f"  当前迭代: {iteration}")
                if feedback:
                    print(f"  反馈: {feedback}")

            elif node_name == "revise":
                revised = node_output.get("analyses", [])
                print(f"  修订条目数: {len(revised)}")

            elif node_name == "human_flag":
                flagged_path = node_output.get("flagged_path", "")
                print(f"  已标记到人工审核: {flagged_path}")

            elif node_name == "save":
                print("  文章已写入 knowledge/articles/")

    logger.info("=" * 50)
    logger.info("LangGraph 工作流结束")
    logger.info("=" * 50)


if __name__ == "__main__":
    asyncio.run(_main())
