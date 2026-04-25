---
name: github-trending
description: 当需要采集 GitHub 热门开源项目时使用此技能
allowed-tools: Read, Grep, Glob, WebFetch
---

# GitHub Trending 采集技能

## 使用场景

- 采集 GitHub 本周/本日热门开源项目
- 筛选 AI / LLM / Agent 领域的优质项目
- 为 AI 知识库提供原始数据输入

## 执行步骤

### 第 1 步：搜索热门仓库

调用 GitHub Search API，按以下参数搜索：

```
GET https://api.github.com/search/repositories
  ?q=topic:ai+topic:llm+created:>{{7天前日期}}
  &sort=stars
  &order=desc
  &per_page=30
```

如 API 返回不足，补充调用：

```
https://api.github.com/search/repositories?q=topic:agent+topic:ai+created:>{{7天前日期}}&sort=stars&order=desc&per_page=30
```

### 第 2 步：提取信息

从 API 返回的每个仓库中提取以下字段：

| 字段         | API 路径                |
| ------------ | ----------------------- |
| 仓库名称     | `full_name`             |
| 仓库 URL     | `html_url`              |
| Stars 数量   | `stargazers_count`      |
| 描述         | `description`           |
| 编程语言     | `language`              |
| 主题标签     | `topics`                |
| 创建时间     | `created_at`            |

### 第 3 步：过滤

纳入规则 — 仓库的 `description` 或 `topics` 中命中以下任一关键词：

| 类别         | 关键词                                                     |
| ------------ | ---------------------------------------------------------- |
| AI 模型      | AI, LLM, GPT, Transformer, Diffusion, Embedding            |
| Agent        | Agent, Multi-agent, Autonomous, Tool-use, Function calling |
| 检索与知识   | RAG, Vector DB, Knowledge, Semantic Search, Memory         |
| 提示与优化   | Prompt, Chain-of-Thought, Fine-tuning, RLHF                |
| 框架与工具   | LangChain, LlamaIndex, CrewAI, OpenCode                    |

排除规则：

- 标题或描述包含 `awesome`, `awesome-list`, `curated-list` 的 Awesome 列表仓库
- 标题或描述包含 `interview`, `tutorial`, `roadmap` 的纯教程/面试仓库
- `stargazers_count` < 5 的低热度仓库

### 第 4 步：去重

- 以 `html_url` 为唯一键去重，相同 URL 保留第一次出现的数据
- 跨多次 API 调用合并结果时，同名仓库（`full_name` 相同）视为重复

### 第 5 步：撰写中文摘要

为每个仓库生成 1-2 句中文字摘要，遵循以下公式：

> **项目名** + **做什么**（核心功能一句话） + **为什么值得关注**（技术亮点或应用价值）

要求：
- 摘要基于 `description` 字段内容，不凭空编造
- 如果 `description` 为空，使用 `topics` 标签辅助推断
- 不夸大、不杜撰功能

### 第 6 步：排序取 Top 15

- 按 `stargazers_count` 降序排列
- 取前 15 条作为最终输出
- 如果筛选后有效条目不足 15 条，返回全部有效条目

### 第 7 步：输出 JSON

将结果写入 `knowledge/raw/github-trending-YYYY-MM-DD.json`，其中日期为当天实际日期。

## 注意事项

- 使用 `WebFetch` 工具调用 GitHub API，不执行任何 Shell 命令
- GitHub API 无需认证即可访问，但速率限制较低（60 次/小时），建议合并查询减少请求次数
- `description` 字段可能为 `null`，需判空处理
- 摘要必须基于实际数据生成，禁止编造
- 同一天多次采集时，后一次应覆盖前一次的同名文件
- 筛选后有效条目数不到 15 条时，不强行凑数

## 输出格式

```json
{
  "source": "github-trending",
  "skill": "github-trending",
  "collected_at": "YYYY-MM-DDThh:mm:ssZ",
  "items": [
    {
      "name": "owner/repo-name",
      "url": "https://github.com/owner/repo-name",
      "summary": "项目名 + 做什么 + 为什么值得关注（1-2 句中文字摘要）",
      "stars": 1234,
      "language": "Python",
      "topics": ["ai", "llm", "agent"]
    }
  ]
}
```

### 字段说明

| 字段           | 类型     | 说明                                    |
| -------------- | -------- | --------------------------------------- |
| `source`       | `string` | 固定值 `"github-trending"`              |
| `skill`        | `string` | 固定值 `"github-trending"`              |
| `collected_at`  | `string` | 采集时间 (ISO 8601)                     |
| `items[].name`  | `string` | 仓库全名 `owner/repo`                   |
| `items[].url`   | `string` | 仓库链接                               |
| `items[].summary` | `string` | 中文摘要（1-2 句）                     |
| `items[].stars` | `number` | Stars 数量                              |
| `items[].language` | `string` | 主要编程语言                           |
| `items[].topics` | `string[]` | 仓库主题标签                           |
