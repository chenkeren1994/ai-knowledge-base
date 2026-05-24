#!/usr/bin/env python3
"""端到端测试：验证工作流三条路径。

路径 A：通过（默认阈值 7.0）→ save → knowledge/articles/
路径 B：循环后通过（阈值 7.5，模拟 1 次修订后通过）→ revise → review → save
路径 C：HumanFlag（阈值 9.0，模拟 3 次不通过）→ human_flag → knowledge/flagged/

由于 LLM 评分不可控，本测试通过控制 review 结果来验证路由逻辑。
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

# 确保可以导入 workflows 模块
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from langgraph.graph import END, StateGraph

from workflows.human_flag import human_flag_node
from workflows.nodes import analyze_node, collect_node, organize_node, save_node
from workflows.reviser import revise_node
from workflows.state import KBState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mock Review 节点（控制返回值以触发不同路径）
# ---------------------------------------------------------------------------

def make_mock_review_node(passed_sequence: list[bool], feedback: str = "测试反馈"):
    """创建 mock review 节点，按预设序列返回通过/不通过。

    Args:
        passed_sequence: 每次调用返回的 review_passed 值序列。
        feedback: 不通过时的反馈内容。

    Returns:
        异步节点函数。
    """
    call_count = 0

    async def mock_review(state: KBState) -> dict:
        nonlocal call_count
        iteration = state.get("iteration", 0)
        passed = passed_sequence[call_count % len(passed_sequence)] if call_count < len(passed_sequence) else True
        call_count += 1

        logger.info("[MockReview] iteration=%d, passed=%s", iteration, passed)
        return {
            "review_passed": passed,
            "review_feedback": feedback if not passed else "",
            "iteration": iteration + 1,
        }

    return mock_review


def route_after_review(state: KBState) -> str:
    """审核后的 3 路条件路由。"""
    _MAX_ITERATIONS = 3
    passed = state.get("review_passed", False)
    iteration = state.get("iteration", 0)

    if passed:
        return "save"
    if iteration < _MAX_ITERATIONS:
        return "revise"
    return "human_flag"


def build_test_graph(mock_review) -> StateGraph:
    """构建测试用工作流图（跳过真实采集/分析）。"""
    graph = StateGraph(KBState)

    # Mock 节点：直接返回预设数据，不调用外部 API/LLM
    async def mock_collect(state: KBState) -> dict:
        return {"sources": state.get("_test_sources", [])}

    async def mock_analyze(state: KBState) -> dict:
        raw_items = []
        for src in state.get("sources", []):
            raw_items.extend(src.get("items", []))
        analyses = [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "source": "github",
                "popularity": item.get("popularity", 0),
                "description": item.get("description", ""),
                "author": item.get("author", ""),
                "language": item.get("language"),
                "topics": item.get("topics", []),
                "summary": f"AI 相关项目：{item.get('title', '')}",
                "highlights": ["亮点 1", "亮点 2"],
                "relevance": 8,
                "tags": ["LLM", "Agent"],
            }
            for item in raw_items
        ]
        return {"analyses": analyses}

    graph.add_node("collect", mock_collect)
    graph.add_node("analyze", mock_analyze)
    graph.add_node("organize", organize_node)
    graph.add_node("review", mock_review)
    graph.add_node("revise", revise_node)
    graph.add_node("human_flag", human_flag_node)
    graph.add_node("save", save_node)

    graph.add_edge("collect", "analyze")
    graph.add_edge("analyze", "organize")
    graph.add_edge("organize", "review")

    graph.add_conditional_edges(
        "review",
        route_after_review,
        {"save": "save", "revise": "revise", "human_flag": "human_flag"},
    )

    graph.add_edge("revise", "review")
    graph.add_edge("human_flag", END)
    graph.add_edge("save", END)
    graph.set_entry_point("collect")

    return graph.compile()


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

def _make_initial_state() -> KBState:
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


async def test_path_a() -> bool:
    """路径 A：一次通过 → save → knowledge/articles/"""
    print("\n" + "=" * 60)
    print("【路径 A 测试】默认通过 → save → knowledge/articles/")
    print("=" * 60)

    mock_review = make_mock_review_node([True])
    app = build_test_graph(mock_review)

    # 注入模拟数据（通过 _test_sources 传递给 mock 节点）
    initial = _make_initial_state()
    initial["_test_sources"] = [{
        "source": "github",
        "count": 2,
        "status": "ok",
        "items": [
            {"title": "test/repo1", "url": "https://github.com/test/repo1", "description": "AI agent framework", "popularity": 1000, "author": "test", "language": "Python", "topics": ["ai", "agent"]},
            {"title": "test/repo2", "url": "https://github.com/test/repo2", "description": "LLM training toolkit", "popularity": 800, "author": "test", "language": "Python", "topics": ["llm", "training"]},
        ],
    }]

    visited_nodes = []
    async for event in app.astream(initial, stream_mode="updates"):
        for node_name in event:
            visited_nodes.append(node_name)
            print(f"  → {node_name}")

    expected = ["collect", "analyze", "organize", "review", "save"]
    passed = visited_nodes == expected
    print(f"  访问节点: {visited_nodes}")
    print(f"  期望节点: {expected}")
    print(f"  结果: {'✓ 通过' if passed else '✗ 失败'}")
    return passed


async def test_path_b() -> bool:
    """路径 B：1 次不通过后通过 → revise → review → save"""
    print("\n" + "=" * 60)
    print("【路径 B 测试】1 次修订后通过 → revise → review → save")
    print("=" * 60)

    # 第一次不通过，第二次通过
    mock_review = make_mock_review_node([False, True], feedback="摘要需更具体，标签需补充 RAG")
    app = build_test_graph(mock_review)

    initial = _make_initial_state()
    initial["_test_sources"] = [{
        "source": "github",
        "count": 1,
        "status": "ok",
        "items": [
            {"title": "test/rag-tool", "url": "https://github.com/test/rag-tool", "description": "RAG pipeline optimization", "popularity": 500, "author": "test", "language": "Python", "topics": ["rag", "llm"]},
        ],
    }]

    visited_nodes = []
    async for event in app.astream(initial, stream_mode="updates"):
        for node_name in event:
            visited_nodes.append(node_name)
            print(f"  → {node_name}")

    expected = ["collect", "analyze", "organize", "review", "revise", "review", "save"]
    passed = visited_nodes == expected
    print(f"  访问节点: {visited_nodes}")
    print(f"  期望节点: {expected}")
    print(f"  结果: {'✓ 通过' if passed else '✗ 失败'}")
    return passed


async def test_path_c() -> bool:
    """路径 C：3 次不通过 → human_flag → END"""
    print("\n" + "=" * 60)
    print("【路径 C 测试】3 次不通过 → human_flag → knowledge/flagged/")
    print("=" * 60)

    # 3 次都不通过
    mock_review = make_mock_review_node([False, False, False], feedback="质量不达标，需重写")
    app = build_test_graph(mock_review)

    initial = _make_initial_state()
    initial["_test_sources"] = [{
        "source": "github",
        "count": 1,
        "status": "ok",
        "items": [
            {"title": "test/low-quality", "url": "https://github.com/test/low-quality", "description": "Basic AI wrapper", "popularity": 50, "author": "test", "language": "Python", "topics": ["ai"]},
        ],
    }]

    visited_nodes = []
    flagged_path = None
    async for event in app.astream(initial, stream_mode="updates"):
        for node_name, output in event.items():
            visited_nodes.append(node_name)
            print(f"  → {node_name}")
            if node_name == "human_flag":
                flagged_path = output.get("flagged_path", "")

    expected = ["collect", "analyze", "organize", "review", "revise", "review", "revise", "review", "human_flag"]
    path_correct = visited_nodes == expected
    file_exists = Path(flagged_path).exists() if flagged_path else False

    print(f"  访问节点: {visited_nodes}")
    print(f"  期望节点: {expected}")
    print(f"  标记文件: {flagged_path}")
    print(f"  文件存在: {file_exists}")
    print(f"  结果: {'✓ 通过' if path_correct and file_exists else '✗ 失败'}")
    return path_correct and file_exists


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

async def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    results = []
    results.append(("路径 A（一次通过）", await test_path_a()))
    results.append(("路径 B（修订后通过）", await test_path_b()))
    results.append(("路径 C（HumanFlag）", await test_path_c()))

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
