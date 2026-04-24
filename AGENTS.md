# AI Knowledge Base Assistant

## 项目概述

自动从 GitHub Trending 和 Hacker News 采集 AI / LLM / Agent 领域的技术动态，由 AI 进行摘要分析和价值判断，结构化存储为 JSON 知识条目，并通过 Telegram、飞书等多渠道推送分发，帮助开发者高效追踪前沿技术资讯。

## 技术栈

| 类别       | 选型                                                   |
| ---------- | ------------------------------------------------------ |
| 语言       | Python 3.12                                            |
| AI 编排    | [OpenCode](https://github.com/anomalyco/opencode)      |
| 大模型     | 国产大模型（通过 OpenCode 对接）                       |
| 工作流引擎 | [LangGraph](https://github.com/langchain-ai/langgraph) |
| 多渠道分发 | [OpenClaw](https://github.com/anomalyco/openclaw)      |

## 编码规范

- **代码风格**：严格遵循 [PEP 8](https://peps.python.org/pep-0008/)
- **命名约定**：变量、函数、方法统一使用 `snake_case`
- **文档注释**：采用 [Google 风格 docstring](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)
- **日志输出**：禁止使用裸 `print()`，统一使用 `logging` 模块

## 项目结构

```
ai-knowledge-base/
├── .opencode/
│   ├── agents/          # OpenCode Agent 定义
│   │   ├── collector    # 采集 Agent
│   │   ├── analyzer     # 分析 Agent
│   │   └── organizer    # 整理 Agent
│   ├── skills/          # OpenCode Skill 定义
│   │   ├── fetch-github-trending/
│   │   ├── fetch-hackernews/
│   │   ├── summarize/
│   │   ├── distribute-telegram/
│   │   └── distribute-feishu/
│   └── package.json     # OpenCode 插件依赖
├── knowledge/
│   ├── raw/             # 原始采集数据
│   └── articles/        # 结构化知识条目（JSON）
├── src/                 # Python 源码
├── tests/               # 测试用例
└── AGENTS.md            # 本文件
```

## 知识条目 JSON 格式

每个知识条目存储为 `knowledge/articles/{id}.json`，字段说明如下：

| 字段          | 类型     | 必填 | 说明                                           |
| ------------- | -------- | ---- | ---------------------------------------------- |
| `id`          | `string` | 是   | 唯一标识，格式 `{source}-{date}-{slug}`        |
| `title`       | `string` | 是   | 文章/项目标题                                  |
| `source`      | `string` | 是   | 来源平台，枚举值：`github-trending`、`hackernews` |
| `source_url`  | `string` | 是   | 原文链接                                       |
| `author`      | `string` | 否   | 作者/项目所有者                                |
| `summary`     | `string` | 是   | AI 生成的摘要（1-3 句）                        |
| `tags`        | `string[]` | 是 | 标签列表，如 `["LLM", "Agent", "RAG"]`       |
| `relevance`   | `number` | 是   | 相关性评分 (0-10)                               |
| `status`      | `string` | 是   | 处理状态：`pending` / `analyzed` / `published` |
| `published_at`| `string` | 否   | 发布时间 (ISO 8601)                             |
| `created_at`  | `string` | 是   | 入库时间 (ISO 8601)                             |
| `updated_at`  | `string` | 是   | 最后更新时间 (ISO 8601)                         |

**示例**：

```json
{
  "id": "hackernews-2026-04-24-agent-memory",
  "title": "Building Long-Term Memory for AI Agents",
  "source": "hackernews",
  "source_url": "https://example.com/article",
  "author": "johndoe",
  "summary": "A deep dive into memory architectures for autonomous agents, covering vector stores, summarization chains, and hybrid approaches.",
  "tags": ["Agent", "Memory", "RAG"],
  "relevance": 8,
  "status": "published",
  "published_at": "2026-04-23T10:00:00Z",
  "created_at": "2026-04-24T08:00:00Z",
  "updated_at": "2026-04-24T08:00:00Z"
}
```

## Agent 角色概览

| 角色   | 名称       | 职责                                            | 触发方式      |
| ------ | ---------- | ----------------------------------------------- | ------------- |
| 采集   | collector  | 从 GitHub Trending、Hacker News 抓取原始数据     | 定时 / 手动   |
| 分析   | analyzer   | 清洗数据、AI 提取摘要、评分、打标签              | 采集完成后    |
| 整理   | organizer  | 结构化写入 JSON、通过 Telegram / 飞书分发推送    | 分析完成后    |

### 采集 Agent (collector)

- 调用 GitHub Trending API 和 Hacker News API
- 按关键词过滤（AI、LLM、Agent、RAG、Prompt 等）
- 去重后将原始数据写入 `knowledge/raw/`

### 分析 Agent (analyzer)

- 读取 `knowledge/raw/` 中的待处理数据
- 调用大模型生成摘要和标签
- 估算相关性评分
- 输出结构化中间结果

### 整理 Agent (organizer)

- 将分析结果写入 `knowledge/articles/{id}.json`
- 更新条目状态为 `published`
- 通过 OpenClaw 推送到 Telegram 频道和飞书群

## 红线（绝对禁止）

1. **禁止硬编码密钥** — API Token、Webhook URL 等敏感信息必须通过环境变量或 `.env` 注入，`.env` 文件必须加入 `.gitignore`
2. **禁止向大模型发送原始密钥或用户隐私数据**
3. **禁止使用裸 `print()`** — 统一使用 `logging` 模块，生产环境禁止输出 debug 级别日志
4. **禁止同步阻塞调用** — 所有网络 IO（API 请求、文件写入、消息推送）必须使用 `async`/`await`
5. **禁止跳过数据校验** — 入库前必须校验 JSON Schema，来源 URL 必须可访问
6. **禁止重复推送** — 分发前必须检查 `status` 字段，已 `published` 的条目不可再次推送
7. **禁止修改已发布条目的 `id`** — `id` 生成后即不可变，仅允许更新 `tags`、`relevance`、`summary` 等分析字段
8. **禁止删除 `knowledge/articles/` 中的 JSON 文件** — 如需撤回，将 `status` 改为 `retracted`
