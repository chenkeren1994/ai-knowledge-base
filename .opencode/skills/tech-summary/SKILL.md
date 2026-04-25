---
name: tech-summary
description: 当需要对采集的技术内容进行深度分析总结时使用此技能
allowed-tools: Read, Grep, Glob, WebFetch
---

# 技术深度分析技能

## 使用场景

- 对 Collector 采集的原始数据进行深度分析
- 生成结构化摘要、亮点、评分和标签
- 识别本周技术趋势和新兴概念
- 为 Analyzer Agent 提供标准化的分析工作流

## 执行步骤

### 第 1 步：读取最新采集文件

使用 `Glob` 定位 `knowledge/raw/` 目录下最新的 JSON 文件：

```
knowledge/raw/github-trending-YYYY-MM-DD.json
knowledge/raw/hackernews-YYYY-MM-DD.json
```

按文件名中的日期排序，取最新的一个或多个文件。使用 `Read` 读取全部内容，确认条目数量和格式完整性。

要求：
- 至少 10 条有效条目才触发分析，否则标记为"数据不足"并终止
- 校验每条必填字段（`name`, `url`, `stars`）是否存在且非空

### 第 2 步：逐条深度分析

对每条条目执行以下分析动作：

#### 2.1 原文核实

使用 `WebFetch` 访问条目 `url`，读取仓库 README 或文章正文，获取完整上下文。摘要和亮点必须基于原文实际内容，不得凭 `description` 字段臆断。

#### 2.2 生成摘要（≤ 50 字）

为每条条目撰写 1-2 句中文字摘要，控制在 50 字以内。采用"项目名 + 核心功能 + 关键价值"结构：

```
例：cavemem 为编码助手提供跨 Agent 持久记忆，本地压缩存储+快速检索，兼容 5 种主流 IDE。（35 字）
```

要求：
- 基于原文内容，不使用 Collector 已有摘要
- 不编造功能、不夸大效果
- 控制字数，超出时优先精简修饰词

#### 2.3 提炼技术亮点（2-3 个）

为每条条目提炼 2-3 个技术亮点，用事实说话：

| 要求     | 说明                                 |
| -------- | ------------------------------------ |
| 可验证   | 亮点可在原文中找到对应描述或代码实现 |
| 具体     | 避免"功能强大""设计优秀"等空洞评价  |
| 差异化   | 突出该条目相比同类项目的独特之处     |
| 数据支撑 | 有具体数字时优先引用（如"29k req/s"）|

#### 2.4 评分（1-10 分，附理由）

按评分标准为每条条目打分，并附一句话理由。

| 分数段 | 含义         | 判定标准                                                                 |
| ------ | ------------ | ------------------------------------------------------------------------ |
| 9-10   | 改变格局     | 可能重塑行业的基础模型、范式突破、或引发广泛关注的里程碑项目             |
| 7-8    | 直接有帮助   | 解决实际痛点、可落地的工具/方法论、显著提升效率的实践方案               |
| 5-6    | 值得了解     | 有一定参考价值，但非即时可用或受众较窄的技术动态                         |
| 1-4    | 可略过       | 与核心领域关联较弱、信息量低、或纯营销内容                               |

评分理由格式：`$分数 分 — $一句话理由`

#### 2.5 标签建议

从以下标签库中为每条条目匹配 2-5 个标签：

| 类别         | 可用标签                                                                                         |
| ------------ | ------------------------------------------------------------------------------------------------ |
| 模型与架构   | `LLM`, `Transformer`, `MoE`, `Diffusion`, `Multi-modal`, `Embedding`, `RLHF`, `Fine-tuning`      |
| Agent 相关   | `Agent`, `Multi-agent`, `Tool-use`, `Function calling`, `Autonomous`, `Planning`, `Memory`        |
| 检索与知识   | `RAG`, `Vector DB`, `Knowledge Graph`, `Semantic Search`, `Knowledge Base`                        |
| 提示与优化   | `Prompt Engineering`, `Chain-of-Thought`, `Few-shot`, `Prompt Optimization`                       |
| 框架与工具   | `LangChain`, `LlamaIndex`, `CrewAI`, `AutoGen`, `Dify`, `Flowise`                                |
| 应用场景     | `Coding`, `Code Review`, `Testing`, `Documentation`, `Chatbot`, `Search`, `Data Analysis`        |
| 工程实践     | `Deployment`, `Inference`, `Quantization`, `Evaluation`, `Safety`, `Guardrails`                  |

要求：
- 优先使用标签库已有标签
- 如确需新增标签，在标签后标注 `[custom]`
- 标签必须与条目实际内容相关

### 第 3 步：趋势发现

分析全部条目后，输出本周技术趋势洞察：

#### 3.1 共同主题

识别 2-4 个跨条目的共同主题，格式：

```
主题名称：简短说明（涉及 N 条条目：条目A、条目B、条目C）
```

#### 3.2 新概念

识别本周出现的新技术概念、术语或范式（1-3 个），要求：
- 该概念在标签库中不存在或很少出现
- 有具体项目支撑，非凭空猜测
- 说明概念来源（哪个项目提出/使用的）

### 第 4 步：输出分析结果 JSON

将完整分析结果输出为 JSON，格式见下方"输出格式"部分。结果不直接写入文件（由 Organizer Agent 负责入库），仅输出到对话中。

## 约束

- **评分上限**：15 个项目中，9-10 分不超过 2 个。如果筛选出超过 2 个 9-10 分候选，保留 stars 最高的 2 个，其余降为 8 分
- **摘要字数**：每条摘要严格控制在 50 字以内
- **亮点数量**：每条 2-3 个，不得少于 2 个
- **标签数量**：每条 2-5 个，不得少于 2 个

## 注意事项

- 所有摘要和亮点必须基于 `WebFetch` 获取的原文内容，不得凭空编造
- `WebFetch` 可能超时，单次超时后重试 1 次，仍失败则使用 Collector 的原始摘要作为 fallback，并在评分理由中标注 `[基于原始描述]`
- GitHub README 中的图片/SVG 会丢失，不影响文字内容的分析
- 标签库为建议清单，如实际内容明确涉及标签库外的领域，可使用 `[custom]` 标记新增
- 趋势发现中的"共同主题"和"新概念"必须能在条目摘要或亮点中找到对应支撑

## 输出格式

```json
{
  "source": "github-trending | hackernews",
  "skill": "tech-summary",
  "analyzed_at": "YYYY-MM-DDThh:mm:ssZ",
  "items": [
    {
      "name": "owner/repo-name",
      "url": "https://github.com/owner/repo-name",
      "summary": "中文摘要（≤50 字）",
      "highlights": [
        "高亮 1（具体、可验证、差异化）",
        "高亮 2",
        "高亮 3（可选）"
      ],
      "score": 8,
      "score_reason": "8 分 — 解决 Agent 跨会话记忆丢失的普遍痛点，设计精巧，兼容性广，直接可用。",
      "tags": ["Memory", "RAG", "Agent", "Coding"]
    }
  ],
  "trends": {
    "themes": [
      "Agent 记忆与持久化：本周多个项目聚焦 Agent 跨会话记忆和长期知识保留（涉及 4 条条目：cavemem、mercury-agent、agentodyssey、super-agent）"
    ],
    "new_concepts": [
      {
        "name": "新概念名称",
        "description": "概念说明",
        "source": "来源项目"
      }
    ]
  }
}
```

### 字段说明

| 字段                   | 类型       | 说明                                                       |
| ---------------------- | ---------- | ---------------------------------------------------------- |
| `source`               | `string`   | 继承自输入数据                                              |
| `skill`                | `string`   | 固定值 `"tech-summary"`                                     |
| `analyzed_at`           | `string`   | 分析时间 (ISO 8601)                                         |
| `items[].name`          | `string`   | 仓库全名，继承自输入                                         |
| `items[].url`           | `string`   | 仓库链接，继承自输入                                         |
| `items[].summary`       | `string`   | 中文摘要（≤ 50 字）                                         |
| `items[].highlights`    | `string[]` | 技术亮点（2-3 个）                                          |
| `items[].score`         | `number`   | 评分（1-10）                                                |
| `items[].score_reason`  | `string`   | 评分理由（一句话）                                          |
| `items[].tags`          | `string[]` | 标签列表（2-5 个）                                          |
| `trends.themes`         | `string[]` | 跨条目共同主题（2-4 个）                                    |
| `trends.new_concepts`   | `object[]` | 新概念列表（`name`, `description`, `source`）               |
