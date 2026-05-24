#!/usr/bin/env python3
"""Router 路由模式：两层意图分类 + 三种处理策略。

**第一层** — 关键词快速匹配（零成本，不调 LLM）
**第二层** — LLM 分类兜底（处理模糊意图）

三种意图：

* ``github_search`` — 调用 GitHub Search API
* ``knowledge_query`` — 检索本地知识库
* ``general_chat`` — 调用 LLM 直接回答

用法::

    import asyncio
    from patterns.router import route

    async def main():
        print(await route("GitHub 上有什么 AI 项目？"))
        print(await route("什么是 RAG？"))

    asyncio.run(main())
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

# 确保可以 import workflow 包
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

ARTICLES_DIR = _PROJECT_ROOT / "knowledge" / "articles"
INDEX_PATH = ARTICLES_DIR / "index.json"

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
GITHUB_AI_QUERY = "ai OR llm OR agent OR rag"

# 第一层关键词映射（按优先级排序）
_KEYWORD_RULES: list[tuple[str, list[str]]] = [
    (
        "github_search",
        [
            "github", "repo", "repository", "仓库", "开源项目",
            "star", "stars", "trending", "coding", "source code",
        ],
    ),
    (
        "knowledge_query",
        [
            "知识库", "knowledge base", "文章", "article",
            "条目", "entry",
        ],
    ),
]

# ---------------------------------------------------------------------------
# 第一层：关键词快速匹配
# ---------------------------------------------------------------------------


def _keyword_match(query: str) -> Optional[str]:
    """基于关键词的第一层意图匹配。

    按优先级依次检测：github_search → knowledge_query。
    两个列表都未命中时返回 ``None``，进入第二层 LLM 分类。

    Args:
        query: 用户查询文本。

    Returns:
        匹配到的 intent 名称，无匹配则返回 ``None``。
    """
    q = query.lower().strip()
    for intent, keywords in _KEYWORD_RULES:
        for kw in keywords:
            if kw in q:
                logger.info("Keyword matched: %r -> %s", kw, intent)
                return intent
    return None


# ---------------------------------------------------------------------------
# 第二层：LLM 分类兜底
# ---------------------------------------------------------------------------

_LLM_CLASSIFY_SYSTEM = """你是一个查询意图分类器。根据用户输入，输出以下三种意图之一：

1. github_search — 用户想搜索 GitHub 上的 AI 开源项目
2. knowledge_query — 用户想查询本地知识库中的技术文章
3. general_chat — 一般性技术对话或问答，不涉及项目搜索和文章检索

请严格以 JSON 格式输出：{"intent": "xxx", "reason": "简短理由"}"""


async def _llm_classify(query: str) -> str:
    """调用 LLM 进行意图分类（第二层兜底）。

    Args:
        query: 用户查询文本。

    Returns:
        intent 名称，非法值时默认回退为 ``general_chat``。
    """
    from workflows.model_client import chat_json

    try:
        result, _usage = await chat_json(
            prompt=query,
            system_prompt=_LLM_CLASSIFY_SYSTEM,
            temperature=0.1,
            max_tokens=200,
        )
    except Exception:
        logger.warning("LLM JSON parse failed for classification, fallback to general_chat")
        return "general_chat"

    intent = result.get("intent", "general_chat")
    if intent not in {"github_search", "knowledge_query", "general_chat"}:
        intent = "general_chat"

    logger.info(
        "LLM classified %r -> %s (reason: %s)",
        query, intent, result.get("reason", ""),
    )
    return intent


# ---------------------------------------------------------------------------
# Handler: github_search
# ---------------------------------------------------------------------------


def _handler_github_search(query: str) -> str:
    """处理 ``github_search`` 意图。

    使用 ``urllib.request`` 调用 GitHub Search API，
    query 参数通过 ``urllib.parse.quote`` 编码以处理中文与空格。

    Args:
        query: 用户查询文本。

    Returns:
        格式化的 GitHub 搜索结果。
    """
    params = {
        "q": GITHUB_AI_QUERY,
        "sort": "stars",
        "order": "desc",
        "per_page": "5",
    }
    query_string = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = f"{GITHUB_SEARCH_URL}?{query_string}"

    logger.info("GitHub Search API: %s", url)

    try:
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        return f"[GitHub Search Error] 网络请求失败: {exc}"
    except json.JSONDecodeError as exc:
        return f"[GitHub Search Error] JSON 解析失败: {exc}"
    except Exception as exc:
        return f"[GitHub Search Error] {exc}"

    items = data.get("items", [])
    if not items:
        return f"未找到与 AI 相关的 GitHub 仓库（总结果: {data.get('total_count', 0)}）。"

    lines: list[str] = []
    lines.append(
        "**GitHub 搜索结果** "
        f"({min(len(items), 5)} / {data.get('total_count', 0)} repos):\n"
    )
    for i, item in enumerate(items[:5], 1):
        name = item.get("full_name", "N/A")
        stars = item.get("stargazers_count", 0)
        desc = (item.get("description") or "（无描述）")[:150]
        lang = item.get("language") or ""
        meta = f" ({lang})" if lang else ""
        lines.append(f"{i}. **{name}**{meta} — {stars} stars")
        lines.append(f"   {desc}")
        lines.append(f"   {item.get('html_url', '')}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Handler: knowledge_query
# ---------------------------------------------------------------------------


def _load_article_index() -> list[dict[str, Any]]:
    """加载或构建知识库索引。

    优先从 ``index.json`` 读取，不存在时从 ``articles/*.json`` 构建。

    Returns:
        文章索引列表，每项含 ``id`` / ``title`` / ``summary`` / ``tags`` 等字段。
    """
    if INDEX_PATH.exists():
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("index.json 损坏，从文章文件重建索引")

    index: list[dict[str, Any]] = []
    if not ARTICLES_DIR.exists():
        return index

    for fpath in sorted(ARTICLES_DIR.glob("*.json")):
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

    return index


def _handler_knowledge_query(query: str) -> str:
    """处理 ``knowledge_query`` 意图。

    从本地 ``knowledge/articles/`` 目录中检索匹配条目。

    Args:
        query: 用户查询文本。

    Returns:
        检索结果文本。
    """
    index = _load_article_index()

    if not index:
        return "知识库中暂无文章条目，请先运行采集流水线。"

    # 分词 + 关键词匹配打分
    query_lower = query.lower()
    terms = [t for t in re.split(r"[^\w]+", query_lower) if t and len(t) >= 2]

    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in index:
        score = 0
        searchable = " ".join([
            entry.get("title", ""),
            entry.get("summary", ""),
            " ".join(entry.get("tags", [])),
        ]).lower()

        for term in terms:
            if term in searchable:
                score += 1

        if score > 0:
            scored.append((score, entry))

    # 按匹配分降序，再按 relevance 降序
    scored.sort(key=lambda x: (-x[0], -x[1].get("relevance", 0)))

    if not scored:
        return (
            f"未找到与「{query}」相关的文章。\n"
            f"知识库共 {len(index)} 篇文章，建议尝试更精确的关键词。"
        )

    top_n = min(5, len(scored))
    lines: list[str] = []
    lines.append(
        f"**知识库检索结果** "
        f"({top_n} 条命中，共 {len(index)} 篇):\n"
    )
    for i, (score, entry) in enumerate(scored[:top_n], 1):
        title = entry.get("title", "N/A")
        summary = (entry.get("summary") or "（无摘要）")[:150]
        tags = "、".join(entry.get("tags", []))
        relevance = entry.get("relevance", 0)

        lines.append(
            f"{i}. **{title}** "
            f"(相关性 {relevance}/10, 匹配 {score} 词)"
        )
        lines.append(f"   {summary}")
        if tags:
            lines.append(f"   标签: {tags}")
        if entry.get("source_url"):
            lines.append(f"   {entry['source_url']}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Handler: general_chat
# ---------------------------------------------------------------------------


async def _handler_general_chat(query: str) -> str:
    """处理 ``general_chat`` 意图。

    调用 LLM 直接回复用户的技术问答。

    Args:
        query: 用户查询文本。

    Returns:
        LLM 回复文本。
    """
    from workflows.model_client import chat

    text, usage = await chat(
        prompt=query,
        system_prompt=(
            "你是一个 AI 技术助手，擅长 LLM、Agent、RAG、深度学习等领域。"
            "请简洁、准确地回答用户问题，控制在 200 字以内。"
        ),
        temperature=0.7,
        max_tokens=1024,
    )
    logger.info("general_chat: %d tokens", usage.total_tokens)
    return text


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------


async def route(query: str) -> str:
    """统一路由入口。

    两层策略决定意图：

    1. **关键词快速匹配** — 检测 ``github`` / ``知识库`` 等关键词，零 LLM 成本
    2. **LLM 分类兜底** — 关键词无匹配时，由 LLM 判断意图

    Args:
        query: 用户查询文本。

    Returns:
        处理结果文本。
    """
    if not query or not query.strip():
        return "请输入查询内容。"

    query = query.strip()
    logger.info("=" * 50)
    logger.info("Routing: %s", query)

    # ---- 第一层：关键词匹配 ----
    intent = _keyword_match(query)

    # ---- 第二层：LLM 分类兜底 ----
    if intent is None:
        logger.info("No keyword match, falling back to LLM classification")
        try:
            intent = await _llm_classify(query)
        except Exception as exc:
            logger.warning("LLM classification failed: %s", exc)
            intent = "general_chat"

    # ---- 分发 ----
    try:
        if intent == "github_search":
            return _handler_github_search(query)
        elif intent == "knowledge_query":
            return _handler_knowledge_query(query)
        else:
            return await _handler_general_chat(query)
    except Exception as exc:
        logger.exception("Handler %s failed", intent)
        return f"[系统错误] {intent} 处理器执行失败: {exc}"


# ---------------------------------------------------------------------------
# 测试入口
# ---------------------------------------------------------------------------


async def _test_router() -> None:
    """测试 Router 三种意图路由。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    test_cases = [
        ("github_search (keyword)", "最近 GitHub 上有哪些热门的 AI 开源项目？"),
        ("knowledge_query (keyword)", "知识库里有没有关于 Agent 的文章？"),
        ("knowledge_query (keyword)", "查找关于 prompt engineering 的条目"),
        ("general_chat (llm)", "什么是 RAG？请简短解释一下。"),
        ("general_chat (llm)", "LangChain 和 LlamaIndex 有什么区别？"),
    ]

    for label, query in test_cases:
        print(f"\n{'─' * 60}")
        print(f"【{label}】")
        print(f"Query: {query}")
        print(f"{'─' * 60}")
        result = await route(query)
        print(result)

    print(f"\n{'─' * 60}")
    print("测试完成")


if __name__ == "__main__":
    asyncio.run(_test_router())
