#!/usr/bin/env python3
"""LangGraph 工作流共享状态定义。

使用 ``TypedDict`` 定义 ``KBState``，作为所有工作流节点的公共状态容器。
遵循"报告式通信"原则：每个字段均为结构化摘要，而非原始数据全文。

用法::

    from workflows.state import KBState

    def collect_node(state: KBState) -> KBState:
        state["sources"] = [{"source": "github", "count": 5, "status": "ok"}]
        return state
"""

from __future__ import annotations

from typing import TypedDict


class KBState(TypedDict):
    """LangGraph 知识库流水线的共享状态。

    在工作流中按需读写这些字段即可，LangGraph 会自动管理状态持久化和传递。
    """

    # ---- 采集阶段 ----
    sources: list[dict]
    """采集到的原始数据摘要。

    每项为一个 dict，包含：
    - ``source`` (str): 来源标识（``github`` / ``rss``）
    - ``count`` (int): 该来源本次采集到的条目数
    - ``status`` (str): 采集状态（``ok`` / ``empty`` / ``error``）

    示例::

        [
            {"source": "github", "count": 12, "status": "ok"},
            {"source": "rss",   "count": 8,  "status": "ok"},
        ]

    注意：不在此字段中存放原始 API 响应正文，仅保留结构化的 collector 报告。
    """

    # ---- 分析阶段 ----
    analyses: list[dict]
    """LLM 分析后的结构化结果摘要。

    每项为一个 dict，包含：
    - ``id`` (str): 临时 ID（入库前可能被重新编号）
    - ``title`` (str): 项目/文章标题
    - ``source`` (str): 来源平台
    - ``relevance`` (int): 相关性评分 (1-10)
    - ``tags`` (list[str]): 标签列表（2-5 个标准标签）
    - ``token_usage`` (int): 本条分析消耗的 LLM Token 数

    示例::

        [
            {
                "id": "temp-001", "title": "foo/bar",
                "source": "github", "relevance": 8,
                "tags": ["LLM", "Agent"],
                "token_usage": 350,
            },
        ]

    注意：完整摘要和 highlights 文本存放在对应的 articles 条目中，
    此处仅保留评分和标签等关键分析元数据。
    """

    # ---- 整理阶段 ----
    articles: list[dict]
    """格式化、去重后的知识条目摘要。

    每项为一个 dict，包含：
    - ``id`` (str): 最终编号（格式 ``{source}-{YYYYMMDD}-{NNN}``）
    - ``title`` (str): 文章标题
    - ``source`` (str): 来源平台
    - ``source_url`` (str): 原文链接
    - ``relevance`` (int): 相关性评分 (1-10)
    - ``status`` (str): 处理状态（``published`` / ``draft`` / ``retracted``）
    - ``tags`` (list[str]): 标签列表

    示例::

        [
            {
                "id": "github-20260524-001", "title": "foo/bar",
                "source": "github", "source_url": "https://...",
                "relevance": 8, "status": "published",
                "tags": ["LLM", "Agent"],
            },
        ]

    注意：完整的 summary / highlights 存储在 ``knowledge/articles/{id}.json``
    中，此处仅保留用于路由和检索决策的结构化摘要。
    """

    # ---- 审核阶段 ----
    review_feedback: str
    """审核反馈意见。

    审核节点将修改建议写回此字段，供分析节点在下一次迭代中参考。
    空字符串表示无反馈或尚未审核。

    示例::

        "摘要质量偏低（空洞词过多）、tags 遗漏了 'RAG' 标签"
    """

    review_passed: bool
    """审核是否通过。

    - ``True``: 审核通过，可进入下一阶段
    - ``False``: 审核未通过，且 ``iteration < 3`` 时触发重分析循环
    """

    iteration: int
    """当前审核循环次数。

    从 0 开始计数，每次审核不通过时 +1。
    达到上限（3 次）后强制通过，不再重试。

    约束：``0 <= iteration <= 3``。
    """

    # ---- 成本追踪 ----
    cost_tracker: dict
    """Token 用量与成本追踪摘要。

    汇总整个工作流的 LLM 调用成本，包含：
    - ``total_tokens`` (int): 总 Token 消耗
    - ``total_cost_cny`` (float): 总费用（人民币，元）
    - ``records`` (int): LLM 调用次数
    - ``models`` (list[str]): 使用的模型列表（去重）

    示例::

        {
            "total_tokens": 12400,
            "total_cost_cny": 0.0248,
            "records": 15,
            "models": ["deepseek-v4-pro"],
        }
    """
