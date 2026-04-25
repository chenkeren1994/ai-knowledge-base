#!/usr/bin/env python3
"""知识条目 5 维度质量评分工具。

对每条知识条目从摘要质量、技术深度、格式规范、标签精度、空洞词检测
五个维度打分，汇总为加权总分并评定 A/B/C 等级。

用法：python hooks/check_quality.py <json_file> [json_file2 ...]
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from itertools import chain

# ---- 标准标签列表 ----
VALID_TAGS = frozenset({
    # 模型与架构
    "LLM", "Transformer", "MoE", "Diffusion", "Multi-modal", "Embedding",
    "RLHF", "Fine-tuning",
    # Agent 相关
    "Agent", "Multi-agent", "Tool-use", "Function calling",
    "Autonomous", "Planning", "Memory",
    # 检索与知识
    "RAG", "Vector DB", "Knowledge Graph", "Semantic Search", "Knowledge Base",
    # 提示与优化
    "Prompt Engineering", "Chain-of-Thought", "Few-shot", "Prompt Optimization",
    # 框架与工具
    "LangChain", "LlamaIndex", "CrewAI", "AutoGen", "Dify", "Flowise",
    # 应用场景
    "Coding", "Code Review", "Testing", "Documentation", "Chatbot",
    "Search", "Data Analysis",
    # 工程实践
    "Deployment", "Inference", "Quantization", "Evaluation", "Safety", "Guardrails",
})

# ---- 技术关键词（摘要中含这些词有加分奖励） ----
TECH_KEYWORDS = frozenset({
    "LLM", "Agent", "RAG", "向量", "检索", "推理", "记忆", "评估",
    "Transformer", "微调", "嵌入", "Embedding", "Prompt", "提示",
    "API", "框架", "模型", "开源", "协议", "训练", "架构", "量化",
    "部署", "安全", "可观测", "路由", "编排", "压缩", "流式",
})

# ---- 空洞词黑名单 ----
BUZZWORDS_CN = frozenset({
    "赋能", "抓手", "闭环", "打通", "全链路", "底层逻辑",
    "颗粒度", "对齐", "拉通", "沉淀", "强大的", "革命性的",
})
BUZZWORDS_EN = frozenset({
    "groundbreaking", "revolutionary", "game-changing",
    "cutting-edge", "state-of-the-art", "best-in-class",
    "world-class", "industry-leading", "disruptive",
    "paradigm-shifting", "next-generation",
})
BUZZWORDS = frozenset(chain(BUZZWORDS_CN, BUZZWORDS_EN))

# ---- 等级阈值 ----
GRADE_A = 80
GRADE_B = 60


@dataclass
class DimensionScore:
    """单个维度的评分结果。"""
    name: str
    score: float
    max_score: int
    detail: str = ""


@dataclass
class QualityReport:
    """单条条目的质量报告。"""
    filepath: str
    title: str
    dimensions: list[DimensionScore] = field(default_factory=list)
    total: float = 0.0
    grade: str = "C"
    errors: list[str] = field(default_factory=list)

    @property
    def total_max(self) -> int:
        return sum(d.max_score for d in self.dimensions)


def load_json(filepath: Path) -> dict | None:
    """加载并解析 JSON 文件，失败返回 None。"""
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def score_summary_quality(data: dict) -> DimensionScore:
    """摘要质量（满分 25）"""
    dim = DimensionScore(name="摘要质量", score=0, max_score=25)
    raw = data.get("summary", "")
    if not isinstance(raw, str):
        dim.detail = "summary 缺失或非字符串"
        return dim

    length = len(raw)

    # 字数为 0
    if length == 0:
        dim.detail = f"摘要为空 → {dim.score}/{dim.max_score}"
        return dim

    # >= 50 字满分，>= 20 字 15 基础分，< 20 字按比例
    if length >= 50:
        dim.score = 15
        dim.detail = f"摘要 {length} 字 → 基础 15 分"
    elif length >= 20:
        dim.score = 15 * length / 50
        dim.detail = f"摘要 {length} 字 → 基础 {dim.score:.0f} 分"
    else:
        dim.score = max(5, 15 * length / 50)
        dim.detail = f"摘要 {length} 字（偏短）→ 基础 {dim.score:.0f} 分"

    # 技术关键词奖励，每个加 1 分，上限 10 分
    hits = sum(1 for kw in TECH_KEYWORDS if kw.lower() in raw.lower())
    bonus = min(hits, 10)
    dim.score += bonus
    if bonus > 0:
        dim.detail += f"，含 {hits} 个技术关键词 +{bonus} 分"
    dim.score = min(dim.score, dim.max_score)
    return dim


def score_tech_depth(data: dict) -> DimensionScore:
    """技术深度（满分 25），基于 relevance/score 字段 1-10 映射。"""
    dim = DimensionScore(name="技术深度", score=0, max_score=25)
    score = data.get("score") or data.get("relevance")
    if not isinstance(score, (int, float)):
        dim.detail = "缺少 score/relevance 字段"
        return dim
    if score < 1 or score > 10:
        dim.detail = f"score={score} 超出 1-10 范围"
        return dim
    mapped = (score - 1) / 9 * 25
    dim.score = min(mapped, dim.max_score)
    dim.detail = f"score={score} → 映射 {dim.score:.1f}/{dim.max_score}"
    return dim


def score_format(data: dict) -> DimensionScore:
    """格式规范（满分 20），5 项各 4 分。"""
    dim = DimensionScore(name="格式规范", score=0, max_score=20)
    checks = [
        ("id 存在且非空", bool(data.get("id") and str(data["id"]).strip())),
        ("title 存在且非空", bool(data.get("title") and str(data["title"]).strip())),
        ("source_url 合法", isinstance(data.get("source_url"), str)
         and re.match(r"^https?://", data["source_url"])),
        ("status 合法", data.get("status") in {"draft", "review", "published", "archived"}),
        ("时间戳有效", _valid_iso(data.get("created_at")) or _valid_iso(data.get("updated_at"))),
    ]
    passed = [label for label, ok in checks if ok]
    failed = [label for label, ok in checks if not ok]
    dim.score = len(passed) * 4
    dim.detail = f"通过 {len(passed)}/5 项：{', '.join(passed)}"
    if failed:
        dim.detail += f"；未通过：{', '.join(failed)}"
    return dim


def _valid_iso(value: object) -> bool:
    """检查是否为粗略的 ISO 8601 时间字符串。"""
    if not isinstance(value, str):
        return False
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", value))


def score_tag_precision(data: dict) -> DimensionScore:
    """标签精度（满分 15）。"""
    dim = DimensionScore(name="标签精度", score=0, max_score=15)
    tags = data.get("tags", [])
    if not isinstance(tags, list):
        dim.detail = "tags 非列表"
        return dim

    n = len(tags)
    if n == 0:
        dim.detail = "无标签 → 0/15"
        return dim
    if n > 5:
        dim.score = 5
        dim.detail = f"标签过多 ({n} 个) → 5/15"
        return dim

    valid_count = sum(1 for t in tags if t in VALID_TAGS)
    invalid = [t for t in tags if t not in VALID_TAGS]

    # 1-3 个标签最佳（15 分满分），4-5 个基础 10 分
    if n <= 3:
        dim.score = 15
    else:
        dim.score = 10

    # 每个非法标签扣 3 分
    penalty = len(invalid) * 3
    dim.score = max(0, dim.score - penalty)

    dim.detail = f"{n} 个标签（合法 {valid_count}）→ {dim.score:.0f}/{dim.max_score}"
    if invalid:
        dim.detail += f"；非法：{', '.join(invalid)}"
    return dim


def score_buzzword_detection(data: dict) -> DimensionScore:
    """空洞词检测（满分 15），搜 summary + highlights 中的空洞词。"""
    dim = DimensionScore(name="空洞词检测", score=0, max_score=15)
    text = " ".join([
        data.get("summary", ""),
        *data.get("highlights", []),
    ]).lower()

    found = [w for w in BUZZWORDS if w in text]
    penalty = min(len(found) * 5, dim.max_score)
    dim.score = max(0, dim.max_score - penalty)
    if found:
        dim.detail = f"命中 {len(found)} 个空洞词（{', '.join(found)}），扣 {penalty} 分 → {dim.score:.0f}/{dim.max_score}"
    else:
        dim.score = dim.max_score
        dim.detail = "未命中空洞词 → 15/15"
    return dim


def _progress_bar(value: float, total: float, width: int = 20) -> str:
    """绘制文本进度条。"""
    ratio = min(value / total, 1.0) if total > 0 else 0
    filled = int(ratio * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}]"


def evaluate_file(filepath: Path) -> QualityReport:
    """对单个文件执行 5 维度评分。"""
    data = load_json(filepath)
    if data is None:
        report = QualityReport(filepath=str(filepath), title="(无法解析)")
        report.errors.append("JSON 解析失败")
        report.grade = "C"
        return report

    title = str(data.get("title", "") or data.get("name", ""))
    report = QualityReport(filepath=str(filepath), title=title)

    dims = [
        score_summary_quality(data),
        score_tech_depth(data),
        score_format(data),
        score_tag_precision(data),
        score_buzzword_detection(data),
    ]
    report.dimensions = dims
    report.total = sum(d.score for d in dims)

    if report.total >= GRADE_A:
        report.grade = "A"
    elif report.total >= GRADE_B:
        report.grade = "B"
    else:
        report.grade = "C"

    return report


def collect_files(args: list[str]) -> list[Path]:
    """展开命令行参数为文件列表（支持 Glob）。"""
    files: list[Path] = []
    for arg in args:
        path = Path(arg)
        if "*" in arg or "?" in arg or "[" in arg:
            matches = list(Path().glob(arg))
            files.extend(sorted(matches))
        else:
            files.append(path)
    return files


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python hooks/check_quality.py <json_file> [json_file2 ...]",
              file=sys.stderr)
        return 1

    files = collect_files(sys.argv[1:])
    if not files:
        print("ERROR: 未找到任何 JSON 文件", file=sys.stderr)
        return 1

    has_c = False
    for filepath in files:
        report = evaluate_file(filepath)
        total_max = report.total_max

        # 可视化进度条
        bar = _progress_bar(report.total, total_max)
        print(f"\n{filepath}")
        print(f"  标题: {report.title}")
        print(f"  总分: {report.total:.0f}/{total_max} {bar}  等级: {report.grade}")

        for d in report.dimensions:
            sub_bar = _progress_bar(d.score, d.max_score)
            print(f"    {d.name:8s} {d.score:5.1f}/{d.max_score} {sub_bar}")
            if d.detail:
                print(f"             {d.detail}")

        if report.errors:
            for err in report.errors:
                print(f"    ✗ {err}")

        if report.grade == "C":
            has_c = True

    # ---- 汇总 ----
    print(f"\n{'='*50}")
    print(f"文件总数: {len(files)}")
    grades = [evaluate_file(f).grade for f in files]
    print(f"A: {grades.count('A')}  B: {grades.count('B')}  C: {grades.count('C')}")

    return 1 if has_c else 0


if __name__ == "__main__":
    sys.exit(main())
