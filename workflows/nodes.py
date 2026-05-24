#!/usr/bin/env python3
"""LangGraph 工作流节点函数。

五个节点构成审核循环流水线：

.. code-block::

    采集(collect) → 分析(analyze) → 整理(organize) → 审核(review)
                                    ↑                        │
                                    └── iteration < 3 ───────┘
                                          (审核不通过时重分析)
                                                    ↓
                                              保存(save)

每个节点是纯函数：接收 ``KBState``，返回 ``dict``（部分状态更新）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workflows.model_client import chat, chat_json, accumulate_usage
from workflows.state import KBState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ARTICLES_DIR = _PROJECT_ROOT / "knowledge" / "articles"
_INDEX_PATH = _ARTICLES_DIR / "index.json"

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
_GITHUB_AI_QUERY = "ai OR llm OR agent OR rag"

_MAX_ITERATIONS = 3
_MAX_ANALYSIS_CONCURRENCY = 5

# ---------------------------------------------------------------------------
# 系统提示词
# ---------------------------------------------------------------------------

_ANALYZE_SYSTEM_PROMPT = """你是一个 AI 技术分析师。你的任务是分析给定的技术文章或开源项目，输出结构化分析结果。

请严格按照以下 JSON 格式输出，不要包含其他内容：
{
  "summary": "中文摘要（1-3 句），概括核心技术点",
  "highlights": ["亮点1（10-20字）", "亮点2（10-20字）"],
  "relevance": <1-10 整数>,
  "tags": ["标签1", "标签2", "标签3"]
}

评分标准：
- 9-10: 可能重塑行业的基础模型、范式突破、里程碑项目
- 7-8: 解决实际痛点、可落地的工具/方法论
- 5-6: 有一定参考价值，但非即时可用
- 1-4: 关联较弱、信息量低

标签从以下列表中选择 2-5 个（优先使用标准标签，不可杜撰）：
LLM, Transformer, MoE, Diffusion, Multi-modal, Embedding, RLHF, Fine-tuning,
Agent, Multi-agent, Tool-use, Function calling, Autonomous, Planning, Memory,
RAG, Vector DB, Knowledge Graph, Semantic Search, Knowledge Base,
Prompt Engineering, Chain-of-Thought, Few-shot, Prompt Optimization,
LangChain, LlamaIndex, CrewAI, AutoGen, Dify, Flowise,
Coding, Code Review, Testing, Documentation, Chatbot, Search, Data Analysis,
Deployment, Inference, Quantization, Evaluation, Safety, Guardrails"""

_FIX_SYSTEM_PROMPT = """你是一个技术内容编辑。根据审核反馈意见，修正以下知识条目的分析结果。

审核反馈：
{feedback}

请修正以下条目，严格以 JSON 格式输出修正后的完整条目：
{{
  "summary": "修正后的中文摘要（1-3 句）",
  "highlights": ["修正后的亮点1", "修正后的亮点2"],
  "relevance": <修正后的评分 1-10>,
  "tags": ["修正后的标签1", "修正后的标签2"]
}}

修正原则：
1. 严格按照审核反馈的具体建议进行修改，不要过度调整
2. 如果反馈提到摘要问题（如空洞词、信息量低），重写摘要使其更加具体和有技术深度
3. 如果反馈提到标签缺失或错误，根据条目内容补充或更正标签
4. 如果反馈提到评分不合理，根据评分标准重新评估"""

_REVIEW_SYSTEM_PROMPT = """你是一个技术内容审核员。对一批知识条目进行四维度质量审核。

审核维度：
1. **摘要质量**（summary_quality）：摘要是否简洁（1-3 句）、准确、包含核心技术点，无空洞词
2. **标签准确**（tag_accuracy）：标签是否从标准标签库中选择、2-5 个、准确反映条目主题
3. **分类合理**（classification）：relevance 评分是否符合评分标准，无偏高或偏低
4. **一致性**（consistency）：highlights 与 summary/tags 是否一致，无矛盾

请综合四个维度给出审核结论，严格以 JSON 格式输出：
{
  "passed": true/false,
  "overall_score": 0.0-1.0,
  "feedback": "总体反馈意见（如 passed=false 须说明具体问题）",
  "scores": {
    "summary_quality": 0.0-1.0,
    "tag_accuracy": 0.0-1.0,
    "classification": 0.0-1.0,
    "consistency": 0.0-1.0
  }
}

判定规则：
- overall_score >= 0.8 且各维度均 >= 0.7 → passed = true
- 任一维度 < 0.7 → passed = false，feedback 须指出该维度的具体问题"""

# ---------------------------------------------------------------------------
# collect_node
# ---------------------------------------------------------------------------


async def collect_node(state: KBState) -> dict:
    """采集节点：从 GitHub Search API 抓取 AI 相关仓库。

    仅在首次迭代（iteration == 0）执行实际抓取，
    后续迭代复用已有数据。

    Args:
        state: 工作流共享状态。

    Returns:
        包含 ``sources`` 的部分状态更新。
    """
    logger.info("[CollectNode] 开始采集")

    if state.get("iteration", 0) > 0:
        logger.info("[CollectNode] 非首次迭代 (iteration=%d)，跳过采集", state["iteration"])
        return {}

    plan = state.get("plan", {}) or {}
    per_source_limit = int(plan.get("per_source_limit", 10))

    def _fetch() -> list[dict[str, Any]]:
        """同步 HTTP 请求（urllib），放入 executor 避免阻塞事件循环。"""
        params = {
            "q": _GITHUB_AI_QUERY,
            "sort": "stars",
            "order": "desc",
            "per_page": str(per_source_limit),
        }
        url = f"{_GITHUB_SEARCH_URL}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"
        logger.info("[CollectNode] 请求 GitHub Search: %s", url)

        req = urllib.request.Request(
            url,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        items: list[dict[str, Any]] = []
        for repo in data.get("items", []):
            full_name = repo.get("full_name", "")
            items.append({
                "title": full_name,
                "url": repo.get("html_url", f"https://github.com/{full_name}"),
                "description": repo.get("description") or "",
                "popularity": repo.get("stargazers_count", 0),
                "author": full_name.split("/")[0] if "/" in full_name else "",
                "language": repo.get("language"),
                "topics": repo.get("topics", []),
            })
        return items

    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(None, _fetch)

    logger.info("[CollectNode] 采集完成: %d 条", len(items))

    return {
        "sources": [
            {
                "source": "github",
                "count": len(items),
                "status": "ok" if items else "empty",
                "items": items,
            },
        ],
    }

# ---------------------------------------------------------------------------
# analyze_node
# ---------------------------------------------------------------------------


async def analyze_node(state: KBState) -> dict:
    """分析节点：对每条原始数据调用 LLM 生成摘要、标签和评分。

    从 ``sources`` 中提取原始条目，批量并发调用 LLM 分析。
    如有审核反馈（review_feedback 非空），会在用户提示中注入修改建议。

    Args:
        state: 工作流共享状态。

    Returns:
        包含 ``analyses`` 和 ``cost_tracker`` 的部分状态更新。
    """
    logger.info("[AnalyzeNode] 开始分析")

    raw_items: list[dict[str, Any]] = []
    for src in state.get("sources", []):
        raw_items.extend(src.get("items", []))

    if not raw_items:
        logger.warning("[AnalyzeNode] 无原始条目，跳过分析")
        return {"analyses": []}

    feedback = state.get("review_feedback", "")
    iteration = state.get("iteration", 0)
    if feedback and iteration > 0:
        logger.info("[AnalyzeNode] 第 %d 轮重分析，审核反馈: %s", iteration, feedback)

    cost_tracker = state.get("cost_tracker", {}).copy()
    semaphore = asyncio.Semaphore(_MAX_ANALYSIS_CONCURRENCY)

    async def _analyze_one(item: dict[str, Any]) -> dict[str, Any] | None:
        async with semaphore:
            content = (
                f"标题: {item['title']}\n"
                f"描述: {item.get('description', '')}\n"
                f"热度: {item.get('popularity', 0)}"
            )
            if feedback:
                content = f"【审核修改建议】{feedback}\n\n{content}"

            try:
                result, usage = await chat_json(
                    prompt=content,
                    system_prompt=_ANALYZE_SYSTEM_PROMPT,
                    temperature=0.3,
                    max_tokens=1024,
                )
            except Exception as exc:
                logger.error("[AnalyzeNode] LLM 调用失败 (title=%s): %s", item["title"], exc)
                return None

            accumulate_usage(cost_tracker, usage)

            return {
                "title": item["title"],
                "url": item.get("url", ""),
                "source": "github",
                "popularity": item.get("popularity", 0),
                "description": item.get("description", ""),
                "author": item.get("author", ""),
                "language": item.get("language"),
                "topics": item.get("topics", []),
                "summary": result.get("summary", item.get("description", "")),
                "highlights": result.get("highlights", []),
                "relevance": result.get("relevance", 5),
                "tags": result.get("tags", []),
                "token_usage": usage.total_tokens,
            }

    tasks = [_analyze_one(item) for item in raw_items]
    results = await asyncio.gather(*tasks)
    analyses = [r for r in results if r is not None]

    logger.info("[AnalyzeNode] 分析完成: %d/%d 条", len(analyses), len(raw_items))

    return {"analyses": analyses, "cost_tracker": cost_tracker}


# ---------------------------------------------------------------------------
# organize_node
# ---------------------------------------------------------------------------


async def organize_node(state: KBState) -> dict:
    """整理节点：过滤低分条目、URL 去重、生成 ID，可选 LLM 定向修正。

    流程：
    1. 过滤 relevance < 6 的条目
    2. URL 去重（排除已有文章和批次内重复）
    3. 如有审核反馈且 iteration > 0，调用 LLM 做定向修改
    4. 生成唯一 ID、标准化格式

    Args:
        state: 工作流共享状态。

    Returns:
        包含 ``articles`` 的部分状态更新。
    """
    logger.info("[OrganizeNode] 开始整理")

    analyses = state.get("analyses", [])
    if not analyses:
        logger.warning("[OrganizeNode] 无分析结果，跳过整理")
        return {"articles": []}

    plan = state.get("plan", {}) or {}
    relevance_threshold = float(plan.get("relevance_threshold", 0.5))

    # 1. 过滤低分条目
    min_relevance = max(1, min(10, int(round(relevance_threshold * 10))))
    filtered = [a for a in analyses if a.get("relevance", 0) >= min_relevance]
    logger.info("[OrganizeNode] 过滤低分: %d -> %d 条 (threshold=%.1f → min_relevance=%d)", len(analyses), len(filtered), relevance_threshold, min_relevance)

    if not filtered:
        return {"articles": []}

    # 2. URL 去重
    existing_urls = _load_existing_urls()
    seen_urls: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for a in filtered:
        url = a.get("url", "")
        if url in existing_urls or url in seen_urls:
            logger.info("[OrganizeNode] URL 去重跳过: %s", url)
            continue
        seen_urls.add(url)
        deduped.append(a)

    logger.info("[OrganizeNode] 去重后: %d 条", len(deduped))

    # 3. 定向修正（有反馈且非首次迭代时）
    feedback = state.get("review_feedback", "")
    iteration = state.get("iteration", 0)
    cost_tracker = state.get("cost_tracker", {}).copy()

    if feedback and iteration > 0 and deduped:
        logger.info("[OrganizeNode] 检测到审核反馈，启动 LLM 定向修正 (iteration=%d)", iteration)
        semaphore = asyncio.Semaphore(_MAX_ANALYSIS_CONCURRENCY)

        async def _fix_one(item: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                content = (
                    f"当前条目:\n"
                    f"标题: {item['title']}\n"
                    f"摘要: {item.get('summary', '')}\n"
                    f"亮点: {json.dumps(item.get('highlights', []), ensure_ascii=False)}\n"
                    f"评分: {item.get('relevance', 5)}\n"
                    f"标签: {json.dumps(item.get('tags', []), ensure_ascii=False)}\n"
                    f"描述: {item.get('description', '')}"
                )
                try:
                    fix_prompt = _FIX_SYSTEM_PROMPT.format(feedback=feedback)
                    result, usage = await chat_json(
                        prompt=content,
                        system_prompt=fix_prompt,
                        temperature=0.3,
                        max_tokens=1024,
                    )
                except Exception as exc:
                    logger.error("[OrganizeNode] 修正失败 (title=%s): %s", item["title"], exc)
                    return item

                accumulate_usage(cost_tracker, usage)

                return {
                    **item,
                    "summary": result.get("summary", item.get("summary", "")),
                    "highlights": result.get("highlights", item.get("highlights", [])),
                    "relevance": result.get("relevance", item.get("relevance", 5)),
                    "tags": result.get("tags", item.get("tags", [])),
                    "token_usage": item.get("token_usage", 0) + usage.total_tokens,
                }

        fix_tasks = [_fix_one(a) for a in deduped]
        deduped = await asyncio.gather(*fix_tasks)
        logger.info("[OrganizeNode] LLM 修正完成: %d 条", len(deduped))

    # 4. 生成 ID、标准化
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    used_ids = _load_existing_ids()

    articles: list[dict[str, Any]] = []
    for seq, item in enumerate(deduped, 1):
        new_id = f"github-{date_part}-{seq:03d}"
        while new_id in used_ids:
            seq += 1
            new_id = f"github-{date_part}-{seq:03d}"
        used_ids.add(new_id)

        articles.append({
            "id": new_id,
            "title": item.get("title", ""),
            "source": item.get("source", "github"),
            "source_url": item.get("url", ""),
            "author": item.get("author") or None,
            "summary": item.get("summary", ""),
            "highlights": item.get("highlights", []),
            "tags": item.get("tags", []),
            "relevance": item.get("relevance", 5),
            "status": "published",
            "published_at": None,
            "created_at": now_iso,
            "updated_at": now_iso,
        })

    logger.info("[OrganizeNode] 整理完成: %d 条", len(articles))

    return {"articles": articles, "cost_tracker": cost_tracker}


# ---------------------------------------------------------------------------
# review_node
# ---------------------------------------------------------------------------


async def review_node(state: KBState) -> dict:
    """审核节点：对 articles 进行四维度 LLM 评分，决定是否通过。

    iteration >= 2 时强制通过（避免无限循环）。
    否则调用 LLM 审核，输出 passed / feedback / scores。

    Args:
        state: 工作流共享状态。

    Returns:
        包含 ``review_passed`` / ``review_feedback`` / ``iteration`` 的部分状态更新。
    """
    logger.info("[ReviewNode] 开始审核")

    iteration = state.get("iteration", 0)
    articles = state.get("articles", [])

    # 强制通过机制
    if iteration >= _MAX_ITERATIONS - 1:
        logger.info("[ReviewNode] iteration=%d >= %d，强制通过", iteration, _MAX_ITERATIONS - 1)
        return {
            "review_passed": True,
            "review_feedback": "",
            "iteration": iteration + 1,
        }

    if not articles:
        logger.warning("[ReviewNode] 无条目可审核，视为通过")
        return {
            "review_passed": True,
            "review_feedback": "",
            "iteration": iteration + 1,
        }

    # 构建审核内容（结构化摘要，非全文）
    review_items: list[str] = []
    for a in articles:
        review_items.append(
            f"- [{a['id']}] {a['title']}\n"
            f"  摘要: {a.get('summary', '')}\n"
            f"  亮点: {', '.join(a.get('highlights', []))}\n"
            f"  评分: {a.get('relevance', 5)}\n"
            f"  标签: {', '.join(a.get('tags', []))}"
        )

    content = "请审核以下知识条目：\n\n" + "\n\n".join(review_items)

    cost_tracker = state.get("cost_tracker", {}).copy()

    try:
        result, usage = await chat_json(
            prompt=content,
            system_prompt=_REVIEW_SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=1024,
        )
    except Exception as exc:
        logger.error("[ReviewNode] 审核 LLM 调用失败: %s，默认通过", exc)
        return {
            "review_passed": True,
            "review_feedback": "",
            "iteration": iteration + 1,
        }

    accumulate_usage(cost_tracker, usage)

    passed = result.get("passed", True)
    overall = result.get("overall_score", 1.0)
    feedback = result.get("feedback", "")
    scores = result.get("scores", {})

    logger.info(
        "[ReviewNode] 审核结果: passed=%s, overall=%.2f, "
        "summary=%.2f, tags=%.2f, classification=%.2f, consistency=%.2f",
        passed, overall,
        scores.get("summary_quality", 0),
        scores.get("tag_accuracy", 0),
        scores.get("classification", 0),
        scores.get("consistency", 0),
    )
    if feedback:
        logger.info("[ReviewNode] 反馈: %s", feedback)

    return {
        "review_passed": passed,
        "review_feedback": feedback if not passed else "",
        "iteration": iteration + 1,
        "cost_tracker": cost_tracker,
    }


# ---------------------------------------------------------------------------
# save_node
# ---------------------------------------------------------------------------


async def save_node(state: KBState) -> dict:
    """保存节点：将 articles 写入 knowledge/articles/ 目录。

    同时更新 knowledge/articles/index.json 索引文件，
    包含 id / title / summary / tags / relevance / source / source_url。

    Args:
        state: 工作流共享状态。

    Returns:
        空 dict（保存为副作用，无状态变更）。
    """
    logger.info("[SaveNode] 开始保存")

    articles = state.get("articles", [])
    if not articles:
        logger.warning("[SaveNode] 无条目可保存")
        return {}

    _ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    saved = 0
    for article in articles:
        filepath = _ARTICLES_DIR / f"{article['id']}.json"
        try:
            filepath.write_text(
                json.dumps(article, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            logger.info("[SaveNode] 已保存: %s", filepath.name)
            saved += 1
        except OSError as exc:
            logger.error("[SaveNode] 写入失败: %s — %s", filepath, exc)

    # 更新索引文件
    _rebuild_index()

    logger.info("[SaveNode] 保存完成: %d/%d 条，索引已更新", saved, len(articles))
    return {}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _load_existing_ids() -> set[str]:
    """加载 knowledge/articles/ 中已有文章的 ID 集合。"""
    existing: set[str] = set()
    if not _ARTICLES_DIR.exists():
        return existing
    for fpath in _ARTICLES_DIR.glob("*.json"):
        if fpath.name == "index.json":
            continue
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "id" in data:
                existing.add(data["id"])
        except (json.JSONDecodeError, OSError):
            continue
    return existing


def _load_existing_urls() -> set[str]:
    """加载 knowledge/articles/ 中已有文章的 URL 集合（用于去重）。"""
    urls: set[str] = set()
    if not _ARTICLES_DIR.exists():
        return urls
    for fpath in _ARTICLES_DIR.glob("*.json"):
        if fpath.name == "index.json":
            continue
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            url = data.get("source_url", "")
            if url:
                urls.add(url)
        except (json.JSONDecodeError, OSError):
            continue
    return urls


def _rebuild_index() -> None:
    """重建 knowledge/articles/index.json 索引文件。

    遍历所有文章 JSON，提取 id / title / summary / tags / relevance / source / source_url。
    """
    index: list[dict[str, Any]] = []
    if not _ARTICLES_DIR.exists():
        return

    for fpath in sorted(_ARTICLES_DIR.glob("*.json")):
        if fpath.name == "index.json":
            continue
        try:
            article = json.loads(fpath.read_text(encoding="utf-8"))
            index.append({
                "id": article.get("id", ""),
                "title": article.get("title", ""),
                "summary": article.get("summary", ""),
                "tags": article.get("tags", []),
                "relevance": article.get("relevance", 0),
                "source": article.get("source", ""),
                "source_url": article.get("source_url", ""),
            })
        except (json.JSONDecodeError, OSError):
            continue

    try:
        _INDEX_PATH.write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("[SaveNode] 索引已写入: %d 条", len(index))
    except OSError as exc:
        logger.error("[SaveNode] 索引写入失败: %s", exc)
