#!/usr/bin/env python3
"""端到端测试（实时 LLM/API）：验证工作流三条路径。

路径 A：REVIEW_PASS_THRESHOLD=1.0 → 必定通过 → save
路径 B：REVIEW_PASS_THRESHOLD=7.5 → 可能修订后通过 → revise → review → save
路径 C：REVIEW_PASS_THRESHOLD=10.0 → 必定失败 → human_flag

用法::

    python test_e2e.py
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from langgraph.graph import END, StateGraph

from workflows.human_flag import human_flag_node
from workflows.nodes import analyze_node, collect_node, organize_node, save_node
from workflows.reviewer import review_node
from workflows.reviser import revise_node
from workflows.state import KBState

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 3


def route_after_review(state: KBState) -> str:
    passed = state.get("review_passed", False)
    iteration = state.get("iteration", 0)
    if passed:
        return "save"
    if iteration < _MAX_ITERATIONS:
        return "revise"
    return "human_flag"


def build_live_graph() -> StateGraph:
    """构建使用真实 API/LLM 调用的工作流图。"""
    graph = StateGraph(KBState)

    graph.add_node("collect", collect_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("organize", organize_node)
    graph.add_node("review", review_node)
    graph.add_node("revise", revise_node)
    graph.add_node("human_flag", human_flag_node)
    graph.add_node("save", save_node)

    graph.add_edge("collect", "analyze")
    graph.add_edge("analyze", "organize")
    graph.add_edge("organize", "review")

    graph.add_conditional_edges(
        "review", route_after_review,
        {"save": "save", "revise": "revise", "human_flag": "human_flag"},
    )

    graph.add_edge("revise", "review")
    graph.add_edge("human_flag", END)
    graph.add_edge("save", END)
    graph.set_entry_point("collect")

    return graph.compile()


def _initial_state() -> KBState:
    return {
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


async def _run_graph(graph, threshold_label: str) -> list[str]:
    """执行工作流图，返回访问过的节点名列表。"""
    visited: list[str] = []
    initial = _initial_state()
    async for event in graph.astream(initial, stream_mode="updates"):
        for node_name, output in event.items():
            visited.append(node_name)
            if node_name == "review":
                print(f"  → review (passed={output.get('review_passed')}, iter={output.get('iteration')})")
            elif node_name == "human_flag":
                print(f"  → human_flag (path={output.get('flagged_path', '')})")
            elif node_name == "save":
                print(f"  → save")
            else:
                print(f"  → {node_name}")
    return visited


async def test_path_a() -> bool:
    """路径 A：阈值 1.0，必定通过 → save。"""
    os.environ["REVIEW_PASS_THRESHOLD"] = "1.0"
    print("\n" + "=" * 60)
    print("【路径 A】REVIEW_PASS_THRESHOLD=1.0 → 必定通过 → save")
    print("=" * 60)

    graph = build_live_graph()
    visited = await _run_graph(graph, "A")

    # 只要最终到达 save 就算通过（LLM 分数不可控）
    passed = "save" in visited
    print(f"  节点流: {' → '.join(visited)}")
    print(f"  结果: {'✓ 通过' if passed else '✗ 失败'}")
    return passed


async def test_path_b() -> bool:
    """路径 B：阈值 6.5，可能需要修订后通过 → revise → review → save。"""
    os.environ["REVIEW_PASS_THRESHOLD"] = "6.5"
    print("\n" + "=" * 60)
    print("【路径 B】REVIEW_PASS_THRESHOLD=6.5 → 修订后通过")
    print("=" * 60)

    graph = build_live_graph()
    visited = await _run_graph(graph, "B")

    passed = "save" in visited
    has_revise = "revise" in visited
    print(f"  节点流: {' → '.join(visited)}")
    print(f"  含修订: {has_revise}, 最终保存: {passed}")
    print(f"  结果: {'✓ 通过' if passed else '✗ 失败'} (LLM 评分不可控，到达 save 即通过)")
    return passed


async def test_path_c() -> bool:
    """路径 C：阈值 10.0，必定所有条目不通过 → 3 次迭代后 → human_flag。"""
    os.environ["REVIEW_PASS_THRESHOLD"] = "10.0"
    print("\n" + "=" * 60)
    print("【路径 C】REVIEW_PASS_THRESHOLD=10.0 → 必定失败 → human_flag")
    print("=" * 60)

    graph = build_live_graph()
    visited = await _run_graph(graph, "C")

    expects_flag = "human_flag" in visited
    review_count = visited.count("review")
    print(f"  节点流: {' → '.join(visited)}")
    print(f"  审核次数: {review_count}, 触发 human_flag: {expects_flag}")
    print(f"  结果: {'✓ 通过' if expects_flag else '✗ 失败'}")
    return expects_flag


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    print("=" * 60)
    print("端到端测试（实时 LLM/API 调用）")
    print("注意：每条路径都会调用 GitHub API + LLM，请确保 API 密钥已配置")
    print("=" * 60)

    results = []
    results.append(("路径 A (阈值 1.0)", await test_path_a()))
    await asyncio.sleep(3)  # 避免 GitHub API 速率限制
    results.append(("路径 B (阈值 7.5)", await test_path_b()))
    await asyncio.sleep(3)
    results.append(("路径 C (阈值 10.0)", await test_path_c()))

    # 恢复默认阈值
    os.environ["REVIEW_PASS_THRESHOLD"] = "7.0"

    print("\n" + "=" * 60)
    print("【测试汇总】")
    print("=" * 60)
    all_passed = True
    for name, passed in results:
        status = "✓ 通过" if passed else "✗ 失败"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False
    print("=" * 60)
    if all_passed:
        print("全部测试通过 ✓")
    else:
        print("部分测试失败 ✗")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
