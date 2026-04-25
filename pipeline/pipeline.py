#!/usr/bin/env python3
"""四步知识库自动化流水线。

采集 → 分析 → 整理 → 保存

支持从 GitHub Search API 和 RSS 源采集 AI 相关内容，
调用 LLM 进行摘要/评分/标签分析，最终输出结构化知识条目。

用法:
    python pipeline/pipeline.py --sources github,rss --limit 20
    python pipeline/pipeline.py --sources github --limit 5
    python pipeline/pipeline.py --sources rss --limit 10
    python pipeline/pipeline.py --sources github --limit 5 --dry-run
    python pipeline/pipeline.py --verbose

环境变量:
    LLM_PROVIDER: 模型提供商（deepseek / qwen / openai，默认 deepseek）
    DEEPSEEK_API_KEY / QWEN_API_KEY / OPENAI_API_KEY: 对应 API 密钥
    GITHUB_TOKEN: GitHub API 个人访问令牌（可选，提高速率限制）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from model_client import create_provider, chat_with_retry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "knowledge" / "raw"
ARTICLES_DIR = PROJECT_ROOT / "knowledge" / "articles"

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
GITHUB_AI_QUERY = "ai OR llm OR agent OR rag"

RSS_FEED_URL = "https://news.ycombinator.com/rss"

AI_KEYWORDS = frozenset({
    "ai", "llm", "gpt", "agent", "rag", "transformer", "diffusion",
    "embedding", "prompt", "fine-tuning", "rlhf", "vector",
    "knowledge", "semantic", "autonomous", "multi-agent", "tool-use",
    "claude", "chatgpt", "copilot", "openai", "anthropic", "deepseek",
    "qwen", "gemini", "mistral", "langchain", "llamaindex", "crewai",
    "autogen", "dify", "flowise", "neural", "deep learning",
    "machine learning", "nlp", "language model",
})

# ---------------------------------------------------------------------------
# Step 1: 采集
# ---------------------------------------------------------------------------


async def collect_github_search(
    limit: int = 20,
    client: Optional[httpx.AsyncClient] = None,
) -> list[dict[str, Any]]:
    """从 GitHub Search API 采集 AI 相关仓库。

    Args:
        limit: 最多返回条目数。
        client: 可复用的 httpx 客户端。

    Returns:
        原始条目列表。
    """
    headers = {"Accept": "application/vnd.github.v3+json"}
    github_token = os.getenv("GITHUB_TOKEN", "")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    should_close = client is None
    if client is None:
        client = httpx.AsyncClient(headers=headers, timeout=30.0)

    items: list[dict[str, Any]] = []
    per_page = min(limit, 100)

    try:
        params = {
            "q": GITHUB_AI_QUERY,
            "sort": "stars",
            "order": "desc",
            "per_page": per_page,
        }
        logger.info("Fetching GitHub repos: q=%s per_page=%d", GITHUB_AI_QUERY, per_page)

        resp = await client.get(GITHUB_SEARCH_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

        for repo in data.get("items", []):
            if len(items) >= limit:
                break

            full_name: str = repo.get("full_name", "")
            description: str = repo.get("description") or ""
            topics: list[str] = repo.get("topics", [])
            combined_text = (full_name + " " + description + " " + " ".join(topics)).lower()

            if not _matches_ai_keywords(combined_text):
                continue

            items.append({
                "title": full_name,
                "url": repo.get("html_url", f"https://github.com/{full_name}"),
                "source": "github",
                "popularity": repo.get("stargazers_count", 0),
                "description": description,
                "author": full_name.split("/")[0] if "/" in full_name else "",
                "language": repo.get("language"),
                "topics": topics,
            })

        logger.info("Collected %d GitHub items (after keyword filter)", len(items))
    finally:
        if should_close:
            await client.aclose()

    return items


async def collect_rss(
    limit: int = 20,
    client: Optional[httpx.AsyncClient] = None,
) -> list[dict[str, Any]]:
    """从 RSS 源采集 AI 相关文章。

    使用简易正则表达式解析 RSS XML。

    Args:
        limit: 最多返回条目数。
        client: 可复用的 httpx 客户端。

    Returns:
        原始条目列表。
    """
    should_close = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)

    items: list[dict[str, Any]] = []

    try:
        logger.info("Fetching RSS feed: %s", RSS_FEED_URL)
        resp = await client.get(RSS_FEED_URL)
        resp.raise_for_status()
        xml_text = resp.text

        item_pattern = re.compile(r"<item>(.*?)</item>", re.DOTALL)
        title_pattern = re.compile(r"<title>(.*?)</title>", re.DOTALL)
        link_pattern = re.compile(r"<link>(.*?)</link>", re.DOTALL)
        pubdate_pattern = re.compile(r"<pubDate>(.*?)</pubDate>", re.DOTALL)

        for match in item_pattern.finditer(xml_text):
            if len(items) >= limit:
                break

            block = match.group(1)
            title = _unescape_xml(_extract_first(title_pattern, block))
            link = _extract_first(link_pattern, block)

            if not title or not link:
                continue

            if not _matches_ai_keywords(title.lower()):
                continue

            items.append({
                "title": title,
                "url": link,
                "source": "rss",
                "popularity": 0,
                "description": "",
                "author": "",
                "language": None,
                "topics": [],
            })
    finally:
        if should_close:
            await client.aclose()

    logger.info("Collected %d RSS items (after keyword filter)", len(items))
    return items


def _matches_ai_keywords(text: str) -> bool:
    """检查文本是否命中 AI 关键词（词边界匹配）。"""
    lowered = text.lower()
    for kw in AI_KEYWORDS:
        pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, lowered):
            return True
    return False


def _extract_first(pattern: re.Pattern, text: str) -> str:
    """从文本中提取第一个匹配组的 stripped 内容。"""
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def _unescape_xml(text: str) -> str:
    """反转义 XML 实体字符。"""
    return (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&#39;", "'")
    )


# ---------------------------------------------------------------------------
# Step 2: 分析
# ---------------------------------------------------------------------------

ANALYZE_SYSTEM_PROMPT = """你是一个 AI 技术分析师。你的任务是分析给定的技术文章或开源项目，输出结构化分析结果。

请严格按照以下 JSON 格式输出，不要输出其他内容：
{
  "summary": "中文摘要（1-3 句），概括核心技术点",
  "highlights": ["亮点1（10-20字）", "亮点2（10-20字）"],
  "relevance": <1-10 整数，按评分标准>,
  "tags": ["标签1", "标签2", "标签3"]
}

评分标准：
- 9-10: 可能重塑行业的基础模型、范式突破、里程碑项目
- 7-8: 解决实际痛点、可落地的工具/方法论
- 5-6: 有一定参考价值，但非即时可用
- 1-4: 关联较弱、信息量低

标签从以下列表中选择 2-5 个（优先使用标准标签）：
LLM, Transformer, MoE, Diffusion, Multi-modal, Embedding, RLHF, Fine-tuning,
Agent, Multi-agent, Tool-use, Function calling, Autonomous, Planning, Memory,
RAG, Vector DB, Knowledge Graph, Semantic Search, Knowledge Base,
Prompt Engineering, Chain-of-Thought, Few-shot, Prompt Optimization,
LangChain, LlamaIndex, CrewAI, AutoGen, Dify, Flowise,
Coding, Code Review, Testing, Documentation, Chatbot, Search, Data Analysis,
Deployment, Inference, Quantization, Evaluation, Safety, Guardrails"""


async def analyze_item(
    item: dict[str, Any],
    provider: Any,
    model: str = "",
    dry_run: bool = False,
) -> Optional[dict[str, Any]]:
    """调用 LLM 分析单条原始条目。

    Args:
        item: 原始条目。
        provider: LLMProvider 实例。
        model: 模型名称。
        dry_run: 干跑模式（跳过真实调用）。

    Returns:
        分析后的条目，dry_run 时返回模拟数据。
    """
    if dry_run:
        return _mock_analysis(item)

    content = (
        f"标题: {item['title']}\n"
        f"描述: {item.get('description', '')}\n"
        f"来源: {item['source']}\n"
        f"热度: {item.get('popularity', 0)}"
    )

    messages = [
        {"role": "system", "content": ANALYZE_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]

    try:
        response = await chat_with_retry(
            provider=provider,
            messages=messages,
            model=model,
            max_tokens=1024,
        )
    except RuntimeError as exc:
        logger.error("分析失败 (title=%s): %s", item["title"], exc)
        return None

    try:
        analysis = json.loads(response.content)
    except json.JSONDecodeError:
        logger.warning("LLM 返回非 JSON 格式 (title=%s), 使用原始响应", item["title"])
        analysis = {
            "summary": response.content[:200],
            "highlights": [],
            "relevance": 5,
            "tags": [],
        }

    return {
        **item,
        "summary": analysis.get("summary", item.get("description", "")),
        "highlights": analysis.get("highlights", []),
        "relevance": analysis.get("relevance", 5),
        "tags": analysis.get("tags", []),
    }


def _mock_analysis(item: dict[str, Any]) -> dict[str, Any]:
    """干跑模式：返回模拟分析数据。"""
    return {
        **item,
        "summary": item.get("description", "") or "(dry-run: 模拟摘要)",
        "highlights": ["(dry-run) 模拟亮点"],
        "relevance": 7,
        "tags": ["LLM", "Agent"],
    }


async def analyze_items(
    items: list[dict[str, Any]],
    provider: Any,
    model: str = "",
    dry_run: bool = False,
    max_concurrency: int = 5,
) -> list[dict[str, Any]]:
    """批量分析条目（带并发限制）。

    Args:
        items: 原始条目列表。
        provider: LLMProvider 实例。
        model: 模型名称。
        dry_run: 干跑模式。
        max_concurrency: 最大并发数。

    Returns:
        分析后的条目列表。
    """
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _limited(item: dict[str, Any]) -> Optional[dict[str, Any]]:
        async with semaphore:
            return await analyze_item(item, provider, model, dry_run)

    tasks = [_limited(item) for item in items]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]


# ---------------------------------------------------------------------------
# Step 3: 整理
# ---------------------------------------------------------------------------


def _generate_id(source: str, seq: int) -> str:
    """生成知识条目 ID。

    Args:
        source: 来源标识（github / rss）。
        seq: 序号。

    Returns:
        形如 ``github-20260425-001`` 的 ID。
    """
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{source}-{date_part}-{seq:03d}"


def _make_slug(title: str, max_words: int = 5, max_chars: int = 60) -> str:
    """基于标题生成短标识。

    Args:
        title: 原始标题。
        max_words: 最多保留词数。
        max_chars: 最大字符数。

    Returns:
        slug 字符串。
    """
    slug = re.sub(r"[^a-zA-Z0-9\s-]", "", title.lower())
    words = [w for w in re.split(r"[\s-]+", slug) if w]
    words = words[:max_words]
    slug = "-".join(words)
    if len(slug) > max_chars:
        slug = slug[:max_chars].rstrip("-")
    return slug


def _validate_article(article: dict[str, Any]) -> list[str]:
    """校验文章必填字段。

    Args:
        article: 文章字典。

    Returns:
        错误信息列表，为空表示通过。
    """
    errors: list[str] = []
    required = ["id", "title", "source", "source_url", "summary", "tags", "relevance", "status"]

    for field in required:
        if field not in article or article[field] is None:
            errors.append(f"缺少必填字段: {field}")

    if article.get("tags") and not isinstance(article["tags"], list):
        errors.append("tags 应为列表")

    if "relevance" in article and not isinstance(article["relevance"], (int, float)):
        errors.append("relevance 应为数字")

    if article.get("source") not in {"github", "rss"}:
        errors.append(f"source 非法: {article.get('source')!r}")

    if article.get("status") not in {"draft", "review", "published", "archived", "retracted"}:
        errors.append(f"status 非法: {article.get('status')!r}")

    return errors


def _load_existing_ids() -> set[str]:
    """加载 knowledge/articles/ 中已有文章的 ID 列表。"""
    existing: set[str] = set()
    if not ARTICLES_DIR.exists():
        return existing
    for fpath in ARTICLES_DIR.glob("*.json"):
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "id" in data:
                existing.add(data["id"])
        except (json.JSONDecodeError, OSError):
            continue
    return existing


def organize_items(
    analyzed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """整理去重、生成 ID、格式标准化、校验。

    Args:
        analyzed: 分析后的条目列表。

    Returns:
        整理后的文章列表。
    """
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    existing_ids = _load_existing_ids()
    used_ids: set[str] = set()
    seen_urls: set[str] = set()
    articles: list[dict[str, Any]] = []
    seq = 1

    for item in analyzed:
        url = item.get("url", "")
        if url in seen_urls:
            logger.info("URL 去重跳过: %s", url)
            continue
        seen_urls.add(url)

        item_source = item.get("source", "unknown")
        new_id = _generate_id(item_source, seq)

        while new_id in existing_ids or new_id in used_ids:
            seq += 1
            new_id = _generate_id(item_source, seq)

        used_ids.add(new_id)

        article = {
            "id": new_id,
            "title": item.get("title", ""),
            "source": item_source,
            "source_url": url,
            "author": item.get("author") or None,
            "summary": item.get("summary", ""),
            "highlights": item.get("highlights", []),
            "tags": item.get("tags", []),
            "relevance": item.get("relevance", 5),
            "status": "published",
            "published_at": None,
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        errors = _validate_article(article)
        if errors:
            logger.warning("校验失败 (id=%s): %s", new_id, "; ".join(errors))
            continue

        articles.append(article)
        seq += 1

    logger.info("整理完成: %d 条 (已去重 %d 条)", len(articles), len(analyzed) - len(articles))
    return articles


# ---------------------------------------------------------------------------
# Step 4: 保存
# ---------------------------------------------------------------------------


def save_articles(articles: list[dict[str, Any]], dry_run: bool = False) -> int:
    """将文章保存为独立 JSON 文件到 knowledge/articles/。

    Args:
        articles: 文章列表。
        dry_run: 干跑模式（仅模拟写入）。

    Returns:
        写入文件数。
    """
    if not ARTICLES_DIR.exists():
        ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    saved = 0
    for article in articles:
        filepath = ARTICLES_DIR / f"{article['id']}.json"

        if dry_run:
            logger.info("[DRY-RUN] 模拟写入: %s", filepath)
            logger.info("  title: %s, tags: %s", article["title"], article["tags"])
            saved += 1
            continue

        try:
            filepath.write_text(
                json.dumps(article, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            logger.info("已保存: %s (%s)", filepath, article["title"])
            saved += 1
        except OSError as exc:
            logger.error("写入失败: %s — %s", filepath, exc)

    return saved


# ---------------------------------------------------------------------------
# Pipeline 编排
# ---------------------------------------------------------------------------


class Pipeline:
    """四步知识库自动化流水线编排器。"""

    def __init__(
        self,
        sources: list[str],
        limit: int,
        dry_run: bool = False,
        model: str = "",
    ) -> None:
        """初始化流水线。

        Args:
            sources: 采集源列表，如 ["github", "rss"]。
            limit: 每个源最多采集数。
            dry_run: 干跑模式。
            model: 分析阶段使用的模型名称。
        """
        self.sources = sources
        self.limit = limit
        self.dry_run = dry_run
        self.model = model
        self._provider: Optional[Any] = None

    async def run(self) -> int:
        """执行完整流水线。

        Returns:
            保存的文章数。
        """
        logger.info("=" * 50)
        logger.info("流水线启动: sources=%s, limit=%d, dry_run=%s",
                     ",".join(self.sources), self.limit, self.dry_run)
        logger.info("=" * 50)

        # Step 1: 采集
        raw_items = await self._collect()
        if not raw_items:
            logger.warning("采集阶段未获取到任何条目，流水线终止")
            return 0

        # 保存原始数据
        self._save_raw(raw_items)

        # Step 2: 分析
        analyzed = await self._analyze(raw_items)
        if not analyzed:
            logger.warning("分析阶段未产出有效结果，流水线终止")
            return 0

        # Step 3: 整理
        organized = organize_items(analyzed)

        # Step 4: 保存
        saved = save_articles(organized, dry_run=self.dry_run)

        logger.info("=" * 50)
        logger.info("流水线完成: 采集=%d, 分析=%d, 整理=%d, 保存=%d",
                     len(raw_items), len(analyzed), len(organized), saved)
        logger.info("=" * 50)
        return saved

    def _ensure_provider(self) -> Any:
        """确保 LLM Provider 已初始化。"""
        if self._provider is None:
            try:
                self._provider = create_provider()
                logger.info("LLM Provider 就绪: %s (model=%s)",
                            self._provider.name, self.model or self._provider.default_model)
            except ValueError as exc:
                logger.error("创建 LLM Provider 失败: %s", exc)
                raise
        return self._provider

    async def _collect(self) -> list[dict[str, Any]]:
        """Step 1: 从各源采集数据。"""
        all_items: list[dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            if "github" in self.sources:
                items = await collect_github_search(limit=self.limit, client=client)
                all_items.extend(items)

            if "rss" in self.sources:
                items = await collect_rss(limit=self.limit, client=client)
                all_items.extend(items)

        logger.info("采集汇总: %d 条", len(all_items))
        return all_items

    def _save_raw(self, items: list[dict[str, Any]]) -> None:
        """将原始采集数据写入 knowledge/raw/。"""
        if not RAW_DIR.exists():
            RAW_DIR.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        raw_payload = {
            "source": ",".join(self.sources),
            "collected_at": timestamp,
            "items": items,
        }

        for source in self.sources:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            raw_path = RAW_DIR / f"{source}-{date_str}.json"

            if self.dry_run:
                logger.info("[DRY-RUN] 原始数据不写入: %s", raw_path)
                continue

            existing: list[dict] = []
            if raw_path.exists():
                try:
                    existing_data = json.loads(raw_path.read_text(encoding="utf-8"))
                    existing = existing_data.get("items", [])
                except (json.JSONDecodeError, OSError):
                    pass

            existing_urls = {e.get("url") for e in existing}
            new_for_source = [i for i in items if i["source"] == source and i.get("url") not in existing_urls]
            merged = existing + new_for_source

            try:
                raw_path.write_text(
                    json.dumps({**raw_payload, "items": merged}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                logger.info("原始数据已保存: %s (%d+%d 条)", raw_path, len(existing), len(new_for_source))
            except OSError as exc:
                logger.error("写入原始数据失败: %s — %s", raw_path, exc)

    async def _analyze(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Step 2: 调用 LLM 分析每条条目。"""
        if not items:
            return []

        provider = self._ensure_provider()
        model = self.model

        logger.info("开始分析 %d 条条目 (dry_run=%s)...", len(items), self.dry_run)
        results = await analyze_items(
            items=items,
            provider=provider,
            model=model,
            dry_run=self.dry_run,
        )
        logger.info("分析完成: %d/%d 条成功", len(results), len(items))
        return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="AI 知识库自动化流水线 — 采集 → 分析 → 整理 → 保存",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  %(prog)s --sources github,rss --limit 20\n"
            "  %(prog)s --sources github --limit 5\n"
            "  %(prog)s --sources rss --limit 10\n"
            "  %(prog)s --sources github --limit 5 --dry-run\n"
            "  %(prog)s --verbose\n"
        ),
    )
    parser.add_argument(
        "--sources", "-s",
        default="github,rss",
        help="采集源，逗号分隔 (github, rss)，默认 github,rss",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=20,
        help="每个源最多采集条目数，默认 20",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="干跑模式：不调用 LLM 也不写入文件",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="输出 DEBUG 级别详细日志",
    )
    parser.add_argument(
        "--model", "-m",
        default="",
        help="分析阶段使用的模型名称（为空则使用默认模型）",
    )
    return parser.parse_args(argv)


def _setup_logging(verbose: bool) -> None:
    """配置日志格式与级别。"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _validate_args(args: argparse.Namespace) -> None:
    """校验参数合法性。"""
    valid_sources = {"github", "rss"}
    requested = set(args.sources.split(","))
    invalid = requested - valid_sources
    if invalid:
        sys.exit(f"错误: 不支持的采集源 {invalid}，可用: {sorted(valid_sources)}")

    if args.limit < 1:
        sys.exit(f"错误: limit 必须 >= 1，当前值: {args.limit}")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


async def _main(argv: Optional[list[str]] = None) -> int:
    """异步主入口。"""
    args = _parse_args(argv)
    _setup_logging(args.verbose)
    _validate_args(args)
    logger.info("Pipeline 启动: --sources=%s --limit=%d --dry-run=%s --model=%s",
                args.sources, args.limit, args.dry_run, args.model or "(默认)")

    sources_list = [s.strip() for s in args.sources.split(",")]

    pipeline = Pipeline(
        sources=sources_list,
        limit=args.limit,
        dry_run=args.dry_run,
        model=args.model,
    )

    try:
        saved = await pipeline.run()
        return 0 if saved > 0 else 1
    except ValueError as exc:
        logger.error("配置错误: %s", exc)
        return 1
    except httpx.HTTPStatusError as exc:
        logger.error("HTTP 请求失败: %s", exc)
        return 1
    except Exception:
        logger.exception("流水线异常终止")
        return 1


def main(argv: Optional[list[str]] = None) -> int:
    """同步入口（供 setup.py 等调用）。"""
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    sys.exit(main())
