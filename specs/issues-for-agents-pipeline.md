# Issues for AI 知识库 · 三 Agent Pipeline

基于 `specs/agents-prd.md` 拆解，按 tracer bullet 垂直切片排列（依赖顺序）。

---

## Issue 1: 创建三 Agent 定义文件

## What to build

创建 collector / analyzer / organizer 三个 Agent 的 `.opencode/agents/*.md` 定义文件，包含：角色描述、权限矩阵（允许/禁止及原因）、输入输出规范、工作职责、质量自查清单。

## Acceptance criteria

- [ ] `.opencode/agents/collector.md` — 采集 Agent，允许 Read/Grep/Glob/WebFetch，禁止 Write/Edit/Bash
- [ ] `.opencode/agents/analyzer.md` — 分析 Agent，允许 Read/Grep/Glob/WebFetch，禁止 Write/Edit/Bash
- [ ] `.opencode/agents/organizer.md` — 整理 Agent，允许 Read/Grep/Glob/Write/Edit，禁止 WebFetch/Bash
- [ ] 每个 Agent 文件包含：角色、权限表格、工作职责、输入规范、输出格式、质量自查清单

## Blocked by

None - can start immediately

---

## Issue 2: Collector — GitHub Trending 采集 + AI 过滤

## What to build

实现 Collector Agent 端到端采集链路：调用 GitHub Trending API → 过滤 AI/LLM/Agent 关键词 → 提取标题/URL/Stars/描述 → 输出结构化 JSON 到 `knowledge/raw/`。支持去重和热度降序排序。

## Acceptance criteria

- [ ] 从 GitHub Trending（或 GitHub Search API）获取当日 Top 50 项目
- [ ] 按关键词过滤（AI、LLM、Agent、RAG、Prompt、Transformer 等）
- [ ] 输出格式符合 Collector Agent 定义的 JSON Schema（title/url/source/popularity/summary）
- [ ] 结果写入 `knowledge/raw/github-trending-{date}.json`
- [ ] 去重（同来源不出现重复标题）
- [ ] 按 popularity 降序排列

## Blocked by

- Blocked by #1

---

## Issue 3: Analyzer — 读取 raw 数据 + AI 深度分析 + 打标签

## What to build

实现 Analyzer Agent 端到端分析链路：读取 `knowledge/raw/` 中的原始采集数据 → 对每条条目调用 WebFetch 访问原文 → LLM 生成中文摘要 + 提炼亮点 → 按评分标准打分(1-10) → 从标签库选择标签 → 输出结构化分析结果。

## Acceptance criteria

- [ ] 读取 `knowledge/raw/` 中最新的 JSON 文件
- [ ] 对每条条目访问原文（WebFetch）生成 1-3 句中文摘要
- [ ] 提炼 1-2 个技术亮点
- [ ] 按 4 级评分标准（9-10 改变格局 / 7-8 直接有帮助 / 5-6 值得了解 / 1-4 可略过）打分
- [ ] 从 7 大类别标签库中匹配 2-5 个标签
- [ ] 输出 JSON 数组（含 summary/highlights/relevance/tags 新增字段）

## Blocked by

- Blocked by #2

---

## Issue 4: Organizer — 整理分析结果 → 写入标准知识条目

## What to build

实现 Organizer Agent 端到端整理入库链路：接收分析结果 → 去重检查（按 URL/Title） → 生成 ID（`{source}-{date}-{slug}`） → 校验 JSON Schema → 写入 `knowledge/articles/{id}.json` → 状态设为 `published` → 输出待分发列表。

## Acceptance criteria

- [ ] 去重：按 `source_url` 完全相同 + `title` 高度相似两种策略去重
- [ ] ID 生成：格式 `{source}-{date}-{slug}`，slug 基于标题（全小写、空格转连字符、≤60 字符）
- [ ] Schema 校验：所有必填字段（id/title/source/source_url/summary/highlights/tags/relevance/status/created_at/updated_at）完整且类型正确
- [ ] 写入 `knowledge/articles/{id}.json`，每个条目单独一个文件
- [ ] `status` 设为 `published`，`created_at`/`updated_at` 为 ISO 8601
- [ ] 已 `published` 条目不可重复写入为新文件（仅更新可变更字段）
- [ ] 不修改已有条目的 `id` 字段
- [ ] 不删除 `knowledge/articles/` 中的已有文件（撤回改 `status` 为 `retracted`）

## Blocked by

- Blocked by #3

---

## Issue 5: 流水线串联 + 上游失败降级策略（HITL）

## What to build

实现 collector → analyzer → organizer 三 Agent 串行流水线，并设计上游 Agent 失败时的降级策略。此为 HITL 切片：需决策失败模式（中断整条流水线 / 跳过失败步骤继续 / 触发告警后人工介入）。

## Acceptance criteria

- [ ] 流水线按 collector → analyzer → organizer 顺序自动执行
- [ ] 数据传递方式确定（文件 in `knowledge/raw/` → 分析结果 in 对话 → 文件 in `knowledge/articles/`）并文档化
- [ ] 上游失败降级策略决策完成并文档化（中断 / 跳过 / 告警）
- [ ] 重跑策略决策完成（全量重跑 / 从失败点续跑 / 仅重跑失败步骤）

## Blocked by

- Blocked by #4

---

## Issue 6: 定时调度 + 进度追踪

## What to build

实现每日 UTC 0:00 自动触发流水线的 cron 调度机制，并提供跨 Agent 执行进度追踪能力，使流水线运行状态可观测。

## Acceptance criteria

- [ ] 每日 UTC 0:00 自动触发流水线（cron job 或调度器配置）
- [ ] 进度追踪：每个 Agent 执行完毕后上报状态（pending → running → completed / failed）
- [ ] 失败告警：Agent 执行失败时触发通知（日志 / 消息推送）
- [ ] 手动触发：支持独立触发任意 Agent 或完整流水线

## Blocked by

- Blocked by #5
