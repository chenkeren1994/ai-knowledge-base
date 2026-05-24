# AI Knowledge Base Assistant

## 项目概述

自动从 GitHub Trending（Search API）和 Hacker News（RSS）采集 AI / LLM / Agent 领域的技术动态，由 AI 进行摘要分析和价值判断，结构化存储为 JSON 知识条目，并通过 Telegram、飞书等多渠道推送分发，帮助开发者高效追踪前沿技术资讯。

## 技术栈

| 类别       | 选型                                                   |
| ---------- | ------------------------------------------------------ |
| 语言       | Python 3.12+（CI 使用 3.11）                           |
| HTTP 客户端 | [httpx](https://www.python-httpx.org/)（异步）        |
| AI 编排    | [OpenCode](https://github.com/anomalyco/opencode)      |
| 大模型     | 国产大模型 — DeepSeek / Qwen / OpenAI（通过 OpenCode 对接） |
| 工作流引擎 | [LangGraph](https://github.com/langchain-ai/langgraph) |
| 多渠道分发 | [OpenClaw](https://github.com/anomalyco/openclaw)      |
| CI/CD      | GitHub Actions（每日定时触发）                         |

## 编码规范

- **代码风格**：严格遵循 [PEP 8](https://peps.python.org/pep-0008/)
- **命名约定**：变量、函数、方法统一使用 `snake_case`
- **文档注释**：采用 [Google 风格 docstring](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)
- **日志输出**：禁止使用裸 `print()`，统一使用 `logging` 模块
- **所有网络 IO** 必须使用 `async`/`await`（httpx 异步客户端）

## 项目结构

```
ai-knowledge-base/
├── .opencode/                    # OpenCode 配置
│   ├── agents/                   # Agent 角色定义（Markdown）
│   │   ├── collector.md          # 采集 Agent
│   │   ├── analyzer.md           # 分析 Agent
│   │   └── organizer.md          # 整理 Agent
│   ├── skills/                   # Skill 工作流定义
│   │   ├── github-trending/      # GitHub 热门项目采集技能
│   │   ├── tech-summary/         # 技术深度分析技能
│   │   ├── grill-me/             # 方案评审访谈技能
│   │   └── to-issues/            # PRD → GitHub Issues 拆解技能
│   ├── plugins/                  # OpenCode 插件
│   │   └── validate.ts           # 写入后自动 JSON 校验
│   ├── package.json              # 插件依赖（@opencode-ai/plugin）
│   └── .gitignore                # node_modules / 包管理器锁文件
├── .agents/                      # 用户级 Agent 配置（备用）
│   └── skills/to-issues/
├── .github/workflows/
│   └── daily-collect.yml         # 每日定时采集流水线
├── pipeline/                     # 核心流水线模块
│   ├── pipeline.py               # 4 步流水线：采集 → 分析 → 整理 → 保存
│   └── model_client.py           # LLM 统一客户端（多提供商 / 成本追踪 / 重试）
├── hooks/                        # 质量保障工具
│   ├── validate_json.py          # JSON Schema 校验
│   └── check_quality.py          # 5 维度质量评分
├── workflows/                    # 工作流辅助模块
│   └── model_client.py           # 简化 LLM 封装（chat / chat_json）
├── patterns/                     # 设计模式实现
│   └── router.py                 # 两层意图路由（关键词 + LLM 兜底）
├── specs/                        # 设计文档
│   ├── agents-prd.md             # 三 Agent 产品需求文档
│   ├── coding-standards.md       # 编码规范 v0.1
│   └── issues-for-agents-pipeline.md  # 流水线任务拆解
├── knowledge/
│   ├── raw/                      # 原始采集数据（按日和源分文件）
│   └── articles/                 # 结构化知识条目（{id}.json）
├── tests/                        # 测试用例
│   ├── test_cost_tracker.py      # CostTracker 单元测试
│   └── test_pipeline_report.py   # Pipeline 成本报告集成测试
├── requirements.txt              # Python 依赖（httpx）
├── skills-lock.json              # Skill 锁定版本
├── sub-agent-test-log.md         # Sub-Agent 测试日志（2026-04-24）
└── AGENTS.md                     # 本文件
```

## 环境变量

| 变量名               | 必需 | 说明                                                  |
| -------------------- | ---- | ----------------------------------------------------- |
| `LLM_PROVIDER`       | 是   | 模型提供商：`deepseek` / `qwen` / `openai`（默认 deepseek） |
| `DEEPSEEK_API_KEY`   | 否   | DeepSeek API 密钥（provider=deepseek 时必需）         |
| `QWEN_API_KEY`       | 否   | Qwen (DashScope) API 密钥（provider=qwen 时必需）     |
| `OPENAI_API_KEY`     | 否   | OpenAI API 密钥（provider=openai 时必需）             |
| `GITHUB_TOKEN`       | 否   | GitHub 个人访问令牌（提高 API 速率限制）              |

## 核心流水线（Pipeline）

`pipeline/pipeline.py` 实现四步端到端知识库自动化流水线：

```
采集(collect) → 分析(analyze) → 整理(organize) → 保存(save)
```

### Step 1: 采集（`collect_github_search` / `collect_rss`）

- **GitHub**：调用 `https://api.github.com/search/repositories`（`q=ai OR llm OR agent OR rag`），按 stars 降序
- **RSS**：抓取 `https://news.ycombinator.com/rss`，正则解析 XML
- 按 AI 关键词词边界匹配过滤（60+ 关键词覆盖模型、Agent、RAG、框架等领域）
- 提取字段：`title` / `url` / `source` / `popularity` / `description` / `author` / `language` / `topics`

### Step 2: 分析（`analyze_item` / `analyze_items`）

- 调用 LLM（通过 `pipeline/model_client.py` 的 `chat_with_retry`）
- 为每条条目生成：中文 `summary`（1-3 句）、`highlights`（2-3 个技术亮点）、`relevance`（1-10 评分）、`tags`（2-5 个标签）
- 支持 `--dry-run` 模式跳过 LLM 调用
- 最大并发数 5（Semaphore 控制）

### Step 3: 整理（`organize_items`）

- 生成唯一 ID（格式 `{source}-{YYYYMMDD}-{NNN}`）
- URL 去重（基于 `knowledge/articles/` 已有条目和当前批次）
- JSON Schema 校验（必填字段、类型、枚举值）
- 追加时间戳（`created_at` / `updated_at`）

### Step 4: 保存（`save_articles`）

- 每条条目写入 `knowledge/articles/{id}.json`
- 原始数据写入 `knowledge/raw/{source}-{date}.json`（合并已有数据，URL 去重）

### CLI 用法

```bash
# 基础用法
python pipeline/pipeline.py --sources github,rss --limit 20

# 仅 GitHub
python pipeline/pipeline.py --sources github --limit 5

# 干跑模式（不调用 LLM、不写文件）
python pipeline/pipeline.py --sources github --limit 5 --dry-run

# 指定模型和详细日志
python pipeline/pipeline.py --sources github --model qwen-plus --verbose
```

### 相关环境变量

| 变量           | 说明                                          |
| -------------- | --------------------------------------------- |
| `LLM_PROVIDER` | 模型提供商（deepseek / qwen / openai）        |
| `GITHUB_TOKEN` | GitHub API Token（提高速率限制）              |
| `DEEPSEEK_API_KEY` / `QWEN_API_KEY` / `OPENAI_API_KEY` | 对应 API 密钥 |

## LLM 客户端（`pipeline/model_client.py`）

统一的 LLM 调用客户端，抽象了多提供商差异。

### 支持的提供商

| 提供商     | API Base                                          | 默认模型            |
| ---------- | ------------------------------------------------- | ------------------- |
| `deepseek` | `https://api.deepseek.com/v1`                     | `deepseek-v4-pro`   |
| `qwen`     | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus`       |
| `openai`   | `https://api.openai.com/v1`                       | `gpt-4o-mini`       |

### 核心功能

- **`LLMProvider`** 抽象基类 → **`OpenAICompatibleProvider`** 统一实现（httpx 异步调用 OpenAI 兼容 API）
- **`chat_with_retry`**：带指数退避重试的对话函数（最多 3 次重试）
- **`quick_chat`**：简单问答场景的一键调用
- **`CostTracker`**：全局 LLM 成本追踪器（支持国产模型 RMB 定价和 OpenAI USD 定价）
- **`estimate_tokens`** / **`estimate_cost`**：Token 估算和费用预估

### 用法示例

```python
import asyncio
from pipeline.model_client import chat_with_retry, get_provider

async def main():
    provider = get_provider()
    response = await chat_with_retry(
        provider=provider,
        messages=[{"role": "user", "content": "用一句话介绍 RAG"}],
    )
    print(response.content)
    print(f"Tokens: {response.usage.total_tokens}")

asyncio.run(main())
```

## 工作流辅助（`workflows/model_client.py`）

简化的 LLM 客户端封装，自动处理 path 导入和 JSON 解析：

- **`chat(prompt)`** → `(text, Usage)` 元组
- **`chat_json(prompt)`** → 解析后的 `dict` 对象（自动追加 JSON 格式要求、清理 markdown 代码块）

## 路由器（`patterns/router.py`）

两层意图路由模式，实现低成本的查询意图分类：

### 第一层：关键词快速匹配（零 LLM 成本）

| 意图              | 触发关键词                                                     |
| ----------------- | -------------------------------------------------------------- |
| `github_search`   | github, repo, 仓库, 开源项目, star, trending                   |
| `knowledge_query` | 知识库, 文章, 条目, entry                                      |

### 第二层：LLM 分类兜底

关键词无匹配时，调用 LLM 判断意图（`general_chat` / `github_search` / `knowledge_query`）。

### 三种处理策略

| 意图              | 处理方式                                             |
| ----------------- | ---------------------------------------------------- |
| `github_search`   | 调用 GitHub Search API 获取 Top 5 仓库               |
| `knowledge_query` | 检索本地 `knowledge/articles/`，TF-IDF 关键词匹配打分 |
| `general_chat`    | 调用 LLM 直接回答                                    |

## 质量保障工具（`hooks/`）

### JSON 校验（`validate_json.py`）

- 校验必填字段（`id` / `title` / `source_url` / `summary` / `tags` / `status`）
- 验证 ID 格式 `{source}-{YYYYMMDD}-{NNN}`
- 验证 status 枚举（`draft` / `review` / `published` / `archived`）
- 验证 URL 格式、摘要长度（≥20 字）、标签数量（≥1）
- 校验可选字段 `score`（1-10）和 `audience`（`beginner` / `intermediate` / `advanced`）
- 支持 Glob 通配符批量校验

### 质量评分（`check_quality.py`）

5 维度加权评分，满分 100：

| 维度       | 满分 | 评分规则                                                       |
| ---------- | ---- | -------------------------------------------------------------- |
| 摘要质量   | 25   | 字数基准（≥50 字 15 分）+ 技术关键词命中奖励（每词 +1，上限 +10） |
| 技术深度   | 25   | `score`/`relevance` 字段 1→25 线性映射                          |
| 格式规范   | 20   | 5 项各 4 分：id / title / source_url / status / 时间戳         |
| 标签精度   | 15   | 1-3 个标签最佳（15 分），非法标签每项扣 3 分                    |
| 空洞词检测 | 15   | 命中中英文空洞词（赋能、groundbreaking 等）每项扣 5 分          |

等级：**A** (≥80) / **B** (≥60) / **C** (<60)

## OpenCode 插件（`validate.ts`）

监听 `Write` 和 `Edit` 工具的 `tool.execute.after` 钩子，当目标文件匹配 `knowledge/articles/*.json` 时自动执行 `validate_json.py` 校验。校验失败时在输出中追加错误信息，不阻断 Agent 执行。

## CI/CD（`.github/workflows/daily-collect.yml`）

- **触发**：每日 UTC 08:00（cron）+ 手动触发（workflow_dispatch）
- **权限**：`contents: write`（用于提交变更）
- **步骤**：
  1. `checkout` → `setup-python` → `pip install`
  2. 运行 `python pipeline/pipeline.py --sources github,rss --limit 20 --verbose`
  3. 校验全部文章：`python hooks/validate_json.py knowledge/articles/*.json`
  4. 质量检查：`python hooks/check_quality.py knowledge/articles/*.json`
  5. 如有变更，`git commit` + `git push`
- **Secrets**：`LLM_PROVIDER` / `DEEPSEEK_API_KEY` / `QWEN_API_KEY` / `OPENAI_API_KEY` / `GITHUB_TOKEN`

## 知识条目 JSON 格式

每个知识条目存储为 `knowledge/articles/{id}.json`，字段说明如下：

| 字段           | 类型       | 必填 | 说明                                                     |
| -------------- | ---------- | ---- | -------------------------------------------------------- |
| `id`           | `string`   | 是   | 唯一标识，格式 `{source}-{YYYYMMDD}-{NNN}`，生成后不可变 |
| `title`        | `string`   | 是   | 文章/项目标题                                            |
| `source`       | `string`   | 是   | 来源平台，枚举值：`github`、`rss`                        |
| `source_url`   | `string`   | 是   | 原文链接（`https://` 开头）                              |
| `author`       | `string`   | 否   | 作者/项目所有者，无数据时填 `null`                       |
| `summary`      | `string`   | 是   | AI 生成的中文摘要（1-3 句）                              |
| `highlights`   | `string[]` | 是   | 技术亮点列表（2-3 条）                                   |
| `tags`         | `string[]` | 是   | 标签列表，从标签库中选择 2-5 个                          |
| `relevance`    | `number`   | 是   | 相关性评分 (1-10)                                        |
| `status`       | `string`   | 是   | 处理状态：`draft` / `review` / `published` / `archived` / `retracted` |
| `published_at` | `string`   | 否   | 原发布时间 (ISO 8601)，无数据时填 `null`                 |
| `created_at`   | `string`   | 是   | 入库时间 (ISO 8601)，`YYYY-MM-DDThh:mm:ssZ`              |
| `updated_at`   | `string`   | 是   | 最后更新时间 (ISO 8601)                                  |

**状态流转**：

```
draft → review → published → [retracted]
                 ↘ archived
```

**标签库**：

| 类别       | 可用标签                                                                                          |
| ---------- | ------------------------------------------------------------------------------------------------- |
| 模型与架构 | `LLM`, `Transformer`, `MoE`, `Diffusion`, `Multi-modal`, `Embedding`, `RLHF`, `Fine-tuning`       |
| Agent 相关 | `Agent`, `Multi-agent`, `Tool-use`, `Function calling`, `Autonomous`, `Planning`, `Memory`         |
| 检索与知识 | `RAG`, `Vector DB`, `Knowledge Graph`, `Semantic Search`, `Knowledge Base`                         |
| 提示与优化 | `Prompt Engineering`, `Chain-of-Thought`, `Few-shot`, `Prompt Optimization`                        |
| 框架与工具 | `LangChain`, `LlamaIndex`, `CrewAI`, `AutoGen`, `Dify`, `Flowise`                                 |
| 应用场景   | `Coding`, `Code Review`, `Testing`, `Documentation`, `Chatbot`, `Search`, `Data Analysis`         |
| 工程实践   | `Deployment`, `Inference`, `Quantization`, `Evaluation`, `Safety`, `Guardrails`                   |

**示例**：

```json
{
  "id": "github-20260524-001",
  "title": "openclaw/openclaw",
  "source": "github",
  "source_url": "https://github.com/openclaw/openclaw",
  "author": "openclaw",
  "summary": "OpenClaw 是一个跨平台个人 AI 助手开源项目，旨在让用户在任何操作系统和设备上便捷使用 AI 服务。",
  "highlights": [
    "跨平台个人 AI 助手",
    "高社区热度（37 万+ stars）",
    "主打易用性和普适性"
  ],
  "tags": ["LLM", "Agent", "Chatbot", "Deployment"],
  "relevance": 6,
  "status": "published",
  "published_at": null,
  "created_at": "2026-05-24T05:05:28Z",
  "updated_at": "2026-05-24T05:05:28Z"
}
```

## ID 生成规则

格式：`{source}-{YYYYMMDD}-{NNN}`

| 组成部分 | 说明                                                        | 示例        |
| -------- | ----------------------------------------------------------- | ----------- |
| `source` | 来源枚举值：`github`、`rss`                                 | `github`    |
| `date`   | 入库日期，`YYYYMMDD` 格式（UTC）                            | `20260524`  |
| `seq`    | 三位序号，从 001 递增（同日内唯一），生成时自动避开已有 ID  | `001`       |

完整 ID 示例：`github-20260524-001`、`rss-20260524-010`

## Agent 角色概览

| 角色   | 名称       | 职责                                            | 定义文件                          |
| ------ | ---------- | ----------------------------------------------- | --------------------------------- |
| 采集   | collector  | 从 GitHub Trending / Hacker News 抓取原始数据    | `.opencode/agents/collector.md`   |
| 分析   | analyzer   | 清洗数据、AI 提取摘要、评分、打标签              | `.opencode/agents/analyzer.md`    |
| 整理   | organizer  | 结构化写入 JSON、通过 Telegram / 飞书分发推送    | `.opencode/agents/organizer.md`   |

### Agent 权限矩阵

| 权限        | Collector | Analyzer | Organizer |
| ----------- | :-------: | :------: | :-------: |
| `Read`      |    ✅     |    ✅    |    ✅     |
| `Grep`      |    ✅     |    ✅    |    ✅     |
| `Glob`      |    ✅     |    ✅    |    ✅     |
| `WebFetch`  |    ✅     |    ✅    |    ❌     |
| `Write`     |    ❌     |    ❌    |    ✅     |
| `Edit`      |    ❌     |    ❌    |    ✅     |
| `Bash`      |    ❌     |    ❌    |    ❌     |

### 采集 Agent (collector)

- 调用 GitHub Trending API 和 Hacker News API（`WebFetch`）
- 按关键词过滤（AI、LLM、Agent、RAG、Prompt、Transformer、RLHF、Fine-tuning、Vector DB 等 60+ 关键词）
- 提取标题、链接、热度指标（stars/points）、描述、编程语言、主题标签
- 去重后输出结构化 JSON 数据到对话中（不写文件）
- 输出格式：JSON 数组，每项含 `title` / `url` / `source` / `popularity` / `summary`

### 分析 Agent (analyzer)

- 读取 `knowledge/raw/` 中 Collector 产出的原始数据
- 通过 `WebFetch` 访问原文链接，获取完整内容
- 调用 LLM 生成摘要、提炼亮点、按评分标准打分、匹配标签
- 输出结构化分析结果（JSON）到对话中（不写文件）
- 输出格式：在 Collector 基础上追加 `summary` / `highlights` / `relevance` / `tags`

### 整理 Agent (organizer)

- 接收 Analyzer 的分析结果，执行去重、ID 生成、Schema 校验
- 将合规条目写入 `knowledge/articles/{id}.json`
- 更新已有条目（仅更新 `tags` / `relevance` / `summary` / `highlights` / `updated_at`，不修改 `id`）
- 触发分发推送（通过 OpenClaw 推送到 Telegram / 飞书）
- 不访问外部网络（禁止 `WebFetch`）

## 评分标准

| 分数段 | 含义       | 判定标准                                                             |
| ------ | ---------- | -------------------------------------------------------------------- |
| 9-10   | 改变格局   | 可能重塑行业的基础模型、范式突破、或引发广泛关注的里程碑项目         |
| 7-8    | 直接有帮助 | 解决实际痛点、可落地的工具/方法论、显著提升效率的实践方案           |
| 5-6    | 值得了解   | 有一定参考价值，但非即时可用或受众较窄的技术动态                     |
| 1-4    | 可略过     | 与核心领域关联较弱、信息量低、或纯营销内容                           |

评分约束：每批次 9-10 分不超过 2 个。

## 测试

```bash
# 运行 CostTracker 单元测试
python tests/test_cost_tracker.py

# 运行 Pipeline 成本报告集成测试
python tests/test_pipeline_report.py

# 校验全部知识条目
python hooks/validate_json.py knowledge/articles/*.json

# 质量评分
python hooks/check_quality.py knowledge/articles/*.json
```

## 红线（绝对禁止）

1. **禁止硬编码密钥** — API Token、Webhook URL 等敏感信息必须通过环境变量或 `.env` 注入，`.env` 文件必须加入 `.gitignore`
2. **禁止向大模型发送原始密钥或用户隐私数据**
3. **禁止使用裸 `print()`** — 统一使用 `logging` 模块，生产环境禁止输出 debug 级别日志
4. **禁止同步阻塞调用** — 所有网络 IO（API 请求、文件写入、消息推送）必须使用 `async`/`await`
5. **禁止跳过数据校验** — 入库前必须校验 JSON Schema，来源 URL 必须可访问
6. **禁止重复推送** — 分发前必须检查 `status` 字段，已 `published` 的条目不可再次推送
7. **禁止修改已发布条目的 `id`** — `id` 生成后即不可变，仅允许更新 `tags`、`relevance`、`summary`、`highlights` 等分析字段
8. **禁止删除 `knowledge/articles/` 中的 JSON 文件** — 如需撤回，将 `status` 改为 `retracted`
