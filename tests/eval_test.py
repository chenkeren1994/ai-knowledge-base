#!/usr/bin/env python3
"""AI 知识库评估测试。

包含两类测试：
1. **本地结构验证** — 不调用 LLM，验证 EVAL_CASES 结构完整性
2. **LLM-as-Judge** — 调用 LLM 对分析结果打分，断言分数 >= 5

用法::

    # 跳过慢速 LLM 测试
    pytest tests/eval_test.py -v -m "not slow"

    # 包含 LLM 测试（需配置 API Key）
    pytest tests/eval_test.py -v
"""

from __future__ import annotations

import logging
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# 评估用例定义
# ---------------------------------------------------------------------------

EVAL_CASES: list[dict] = [
    {
        "name": "正面案例 — RAG 技术文章",
        "input": (
            "Retrieval-Augmented Generation (RAG) 是一种结合信息检索与文本生成的"
            "技术框架，通过先从外部知识库中检索相关文档片段，再将检索结果注入 LLM 的"
            "上下文窗口，有效缓解了大模型的幻觉问题，并在问答、摘要等场景中"
            "显著提升了事实准确性和可解释性。"
        ),
        "expected": {
            "min_summary_length": 20,
            "keywords": ["RAG", "LLM", "检索", "知识库"],
            "min_relevance": 5,
        },
    },
    {
        "name": "负面案例 — 无关内容（菜谱）",
        "input": (
            "红烧肉是一道经典的中式菜肴，选用五花肉为主料，经过焯水、炒糖色、"
            "慢炖等步骤烹制而成。成品色泽红亮，肥而不腻，入口即化。"
            "配料包括生抽、老抽、冰糖、八角、桂皮、姜片和料酒。"
        ),
        "expected": {
            "min_summary_length": 5,
            "keywords": [],
            "max_relevance": 4,
        },
    },
    {
        "name": "边界案例 — 极短输入",
        "input": "AI",
        "expected": {
            "min_summary_length": 1,
            "keywords": [],
            "max_relevance": 5,
        },
    },
    {
        "name": "正面案例 — Agent 多智能体",
        "input": (
            "Multi-Agent 架构通过将复杂任务分解为多个专业化 Agent 协同完成，"
            "每个 Agent 拥有独立的规划、记忆和工具调用能力。"
            "AutoGen、CrewAI 等框架通过定义 Agent 角色和通信协议，"
            "实现了可扩展的多智能体协作系统，在代码生成、数据分析等领域"
            "展现出比单 Agent 更强的鲁棒性和任务完成率。"
        ),
        "expected": {
            "min_summary_length": 20,
            "keywords": ["Agent", "Multi-Agent", "CrewAI", "AutoGen"],
            "min_relevance": 6,
        },
    },
    {
        "name": "负面案例 — 纯营销软文",
        "input": (
            "我们的产品是市场上最好的 AI 解决方案！拥有革命性的技术，"
            "赋能企业数字化转型，打通全链路数据，提供强大的智能化体验。"
            "立即联系我们获取免费演示！"
        ),
        "expected": {
            "min_summary_length": 5,
            "keywords": [],
            "max_relevance": 3,
        },
    },
]

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _check_keyword_hits(text: str, keywords: list[str]) -> int:
    """统计 keywords 在 text 中的命中数（大小写不敏感）。"""
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in text_lower)


# ---------------------------------------------------------------------------
# 本地结构验证测试（不调用 LLM）
# ---------------------------------------------------------------------------


def test_eval_cases_structure() -> None:
    """验证 EVAL_CASES 列表的结构完整性。"""
    assert isinstance(EVAL_CASES, list), "EVAL_CASES 应为列表"
    assert len(EVAL_CASES) >= 3, f"至少需要 3 个用例，当前 {len(EVAL_CASES)} 个"

    required_keys = {"name", "input", "expected"}
    expected_keys = {
        "min_summary_length", "keywords", "min_relevance",
    }

    for i, case in enumerate(EVAL_CASES):
        # 必填键
        missing = required_keys - set(case)
        assert not missing, f"用例 [{i}] 缺少字段: {missing}"

        # name 非空
        assert isinstance(case["name"], str) and case["name"].strip(), (
            f"用例 [{i}] name 应为非空字符串"
        )

        # input 非空
        assert isinstance(case["input"], str) and case["input"].strip(), (
            f"用例 [{i}] ({case['name']}) input 应为非空字符串"
        )

        # expected 是字典
        expected = case["expected"]
        assert isinstance(expected, dict), (
            f"用例 [{i}] ({case['name']}) expected 应为字典"
        )

        # expected 至少包含正/负面断言的键
        has_min = "min_summary_length" in expected or "max_relevance" in expected
        has_max = "max_relevance" in expected or "min_relevance" in expected
        assert has_min or has_max, (
            f"用例 [{i}] ({case['name']}) expected 至少需要一个范围断言字段"
        )

    # 验证已覆盖三种场景
    names = [c["name"] for c in EVAL_CASES]
    assert any("正面" in n for n in names), "缺少正面案例"
    assert any("负面" in n for n in names), "缺少负面案例"
    assert any("边界" in n for n in names), "缺少边界案例"


def test_eval_cases_keyword_list() -> None:
    """验证每个用例的 keywords 均为列表类型。"""
    for case in EVAL_CASES:
        keywords = case["expected"].get("keywords")
        assert keywords is not None, (
            f"用例 ({case['name']}) expected 缺少 keywords 字段"
        )
        assert isinstance(keywords, list), (
            f"用例 ({case['name']}) keywords 应为列表，实际 {type(keywords).__name__}"
        )
        for kw in keywords:
            assert isinstance(kw, str), (
                f"用例 ({case['name']}) keywords 元素应为 str，"
                f"实际 {type(kw).__name__}: {kw!r}"
            )


def test_keyword_hits_positive_cases() -> None:
    """正面案例的 input 应包含预期关键词。"""
    for case in EVAL_CASES:
        if "正面" not in case["name"]:
            continue
        keywords = case["expected"]["keywords"]
        hits = _check_keyword_hits(case["input"], keywords)
        assert hits >= 1, (
            f"正面用例 ({case['name']}) input 中未找到预期关键词 {keywords}"
        )


def test_negative_case_filters_keywords() -> None:
    """负面案例的 input 不应包含 AI 技术关键词。"""
    for case in EVAL_CASES:
        if "负面" not in case["name"]:
            continue
        keywords = case["expected"]["keywords"]
        if not keywords:  # 预期 0 命中
            hits = _check_keyword_hits(case["input"], keywords)
            assert hits == 0, (
                f"负面用例 ({case['name']}) input 不应包含预期关键词"
            )


# ---------------------------------------------------------------------------
# LLM-as-Judge 测试
# ---------------------------------------------------------------------------


ANALYSIS_SYSTEM_PROMPT = """\
你是一个 AI 知识库内容质量评审专家。你的任务是对输入文本进行分析，
评估其作为 AI 技术知识库条目的价值。

请按以下格式输出（仅输出 JSON，不要包含其他内容）：
{
    "summary": "1-3 句中文摘要（不少于 10 字）",
    "relevance": 1-10 的整数评分,
    "tags": ["标签1", "标签2"],
    "quality_reason": "给出此评分的一句话理由"
}"""

JUDGE_SYSTEM_PROMPT = """\
你是一个技术内容质量评审专家。请对以下 AI 分析结果进行二次评分（1-10），
仅输出一个整数分数，不要包含其他内容。

评分维度：
- 摘要是否准确概括了原文（3 分）
- relevance 评分是否合理（3 分）
- 标签是否与技术内容匹配（2 分）
- 理由是否有说服力（2 分）"""


def _collect_analysis(case: dict, analysis_text: str) -> dict:
    """收集单条分析结果供 judge 使用，失败时返回空字典。"""
    analysis_text = analysis_text.strip()
    if analysis_text.startswith("```"):
        lines = analysis_text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        analysis_text = "\n".join(lines).strip()

    import json as _json

    try:
        result = _json.loads(analysis_text)
    except _json.JSONDecodeError:
        return {}

    if not isinstance(result, dict):
        return {}
    return result


@pytest.mark.asyncio
@pytest.mark.slow
async def test_llm_analysis_quality() -> None:
    """LLM 对每个评估用例进行分析，然后由 Judge 二次打分。

    断言 Judge 评分 >= 5（及格线）。
    """
    api_key = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        pytest.skip("未配置 LLM_API_KEY 或 DEEPSEEK_API_KEY，跳过 LLM 测试")

    from workflows.model_client import chat

    # 只选正面和边界案例（负面案例不期望 LLM 产生有价值分析）
    target_cases = [
        c for c in EVAL_CASES
        if "负面" not in c["name"]
    ]

    assert len(target_cases) >= 2, "至少需要 2 个可分析用例"

    scores: list[int] = []

    for case in target_cases:
        # Step 1: LLM 分析
        analysis_text, usage = await chat(
            prompt=case["input"],
            system_prompt=ANALYSIS_SYSTEM_PROMPT,
            temperature=0.3,
        )

        logging.getLogger(__name__).info(
            "LLM 分析完成 | case=%s | tokens=%s",
            case["name"], usage.total_tokens,
        )

        analysis = _collect_analysis(case, analysis_text)
        assert analysis, (
            f"用例 ({case['name']}) LLM 分析结果解析失败: {analysis_text[:100]}"
        )

        # Step 2: LLM-as-Judge 二次评分
        judge_input = (
            f"原文：\n{case['input']}\n\n"
            f"AI 分析结果：\n"
            f"  - 摘要：{analysis.get('summary', 'N/A')}\n"
            f"  - relevance：{analysis.get('relevance', 'N/A')}\n"
            f"  - 标签：{analysis.get('tags', [])}\n"
            f"  - 评分理由：{analysis.get('quality_reason', 'N/A')}"
        )

        judge_text, judge_usage = await chat(
            prompt=judge_input,
            system_prompt=JUDGE_SYSTEM_PROMPT,
            temperature=0.2,
        )

        logging.getLogger(__name__).info(
            "Judge 评分完成 | case=%s | tokens=%s | raw=%s",
            case["name"], judge_usage.total_tokens, judge_text.strip(),
        )

        try:
            score = int(judge_text.strip())
        except ValueError:
            pytest.fail(
                f"用例 ({case['name']}) Judge 返回非法分值: {judge_text.strip()!r}"
            )

        assert 1 <= score <= 10, (
            f"用例 ({case['name']}) Judge 分值 {score} 不在 1-10 范围"
        )
        assert score >= 5, (
            f"用例 ({case['name']}) Judge 分值 {score} 低于及格线 (5)\n"
            f"原文: {case['input'][:80]}...\n"
            f"分析: {analysis_text[:200]}"
        )

        scores.append(score)

    # 整体质量：平均分 >= 5
    avg_score = sum(scores) / len(scores)
    assert avg_score >= 5, (
        f"LLM-as-Judge 平均分 {avg_score:.1f} 低于 5 分及格线，"
        f"单项分数: {scores}"
    )


@pytest.mark.asyncio
@pytest.mark.slow
async def test_llm_keyword_detection() -> None:
    """验证 LLM 能从正面案例中提取出预期关键词。"""
    api_key = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        pytest.skip("未配置 LLM_API_KEY 或 DEEPSEEK_API_KEY，跳过 LLM 测试")

    from workflows.model_client import chat

    positive_cases = [c for c in EVAL_CASES if "正面" in c["name"]]
    if not positive_cases:
        pytest.skip("无正面案例，跳过关键词检测")

    for case in positive_cases[:2]:  # 最多测 2 个以控制成本
        analysis_text, _ = await chat(
            prompt=case["input"],
            system_prompt=ANALYSIS_SYSTEM_PROMPT,
            temperature=0.3,
        )

        analysis = _collect_analysis(case, analysis_text)
        if not analysis:
            pytest.fail(
                f"用例 ({case['name']}) LLM 分析结果解析失败: {analysis_text[:100]}"
            )

        summary = analysis.get("summary", "")
        tags = analysis.get("tags", [])
        combined = summary + " ".join(tags)

        expected_keywords = case["expected"].get("keywords", [])
        if not expected_keywords:
            continue

        hits = _check_keyword_hits(combined, expected_keywords)
        assert hits >= 1, (
            f"用例 ({case['name']}) LLM 分析结果未命中任何预期关键词:\n"
            f"预期: {expected_keywords}\n"
            f"摘要: {summary}\n"
            f"标签: {tags}"
        )


# ---------------------------------------------------------------------------
# 手动运行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    print("=" * 60)
    print("本地结构验证")
    print("=" * 60)

    test_eval_cases_structure()
    test_eval_cases_keyword_list()
    test_keyword_hits_positive_cases()
    test_negative_case_filters_keywords()
    print("  本地验证全部通过")

    api_key = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    if api_key:
        print()
        print("=" * 60)
        print("LLM-as-Judge 测试")
        print("=" * 60)
        asyncio.run(test_llm_analysis_quality())
        asyncio.run(test_llm_keyword_detection())
        print("  LLM-as-Judge 全部通过")
    else:
        print("  跳过 LLM 测试（未配置 API Key）")

    print()
    print("全部测试通过")
