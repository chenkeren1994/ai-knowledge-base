#!/usr/bin/env python3
"""多 Agent 预算守卫。

提供 ``CostGuard`` 类，在知识库流水线中追踪 LLM 调用成本，
三重保护机制：正常 → 预警 → 超限抛异常。

用法::

    from tests.cost_guard import CostGuard, BudgetExceededError

    guard = CostGuard(budget_yuan=1.0, alert_threshold=0.8)

    guard.record("analyze", {"prompt_tokens": 500, "completion_tokens": 200}, model="deepseek")
    guard.record("organize", {"prompt_tokens": 300, "completion_tokens": 150}, model="deepseek")

    status = guard.check()
    print(status["status"])   # "ok" | "warning"
    print(status["total_cost"])
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class BudgetExceededError(Exception):
    """预算超限异常。

    当累计 LLM 调用成本超过预算上限时抛出。
    """

    def __init__(self, total_cost: float, budget: float) -> None:
        self.total_cost = total_cost
        self.budget = budget
        super().__init__(
            f"预算超限！累计成本 {total_cost:.4f} 元 已超出预算 {budget:.4f} 元"
        )


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class CostRecord:
    """单次 LLM 调用成本记录。

    Attributes:
        timestamp: 调用时间 (UTC)。
        node_name: 发起调用的节点名称。
        prompt_tokens: 输入 token 数。
        completion_tokens: 输出 token 数。
        cost_yuan: 本次调用费用（人民币元）。
        model: 使用的模型名称。
    """

    timestamp: str
    node_name: str
    prompt_tokens: int
    completion_tokens: int
    cost_yuan: float
    model: str = ""


# ---------------------------------------------------------------------------
# CostGuard
# ---------------------------------------------------------------------------


class CostGuard:
    """多 Agent 预算守卫。

    三重保护机制：
    1. **正常** — 累计成本 < alert_threshold * budget，``check()`` 返回 ``"ok"``
    2. **预警** — 累计成本 >= alert_threshold * budget 但未超预算，返回 ``"warning"``
    3. **超限** — 累计成本 > budget，抛出 ``BudgetExceededError``

    Args:
        budget_yuan: 预算上限（人民币元），默认 1.0。
        alert_threshold: 预警比例，达到 budget * alert_threshold 时触发 warning，默认 0.8。
        input_price_per_million: 输入 token 单价（每百万 token / 元），默认 1.0。
        output_price_per_million: 输出 token 单价（每百万 token / 元），默认 2.0。
    """

    def __init__(
        self,
        budget_yuan: float = 1.0,
        alert_threshold: float = 0.8,
        input_price_per_million: float = 1.0,
        output_price_per_million: float = 2.0,
    ) -> None:
        if budget_yuan <= 0:
            raise ValueError(f"budget_yuan 必须为正数，收到 {budget_yuan}")
        if not 0 < alert_threshold <= 1:
            raise ValueError(f"alert_threshold 必须在 (0, 1] 之间，收到 {alert_threshold}")

        self.budget_yuan = budget_yuan
        self.alert_threshold = alert_threshold
        self.input_price_per_million = input_price_per_million
        self.output_price_per_million = output_price_per_million

        self._records: list[CostRecord] = []
        self._total_prompt_tokens: int = 0
        self._total_completion_tokens: int = 0
        self._total_cost_yuan: float = 0.0

    # ------------------------------------------------------------------
    # 记录
    # ------------------------------------------------------------------

    def record(
        self,
        node_name: str,
        usage: dict,
        model: str = "",
    ) -> CostRecord:
        """记录一次 LLM 调用成本。

        根据 usage 中的 token 计数和定价参数自动计算本次费用，
        追加到内部记录表。

        Args:
            node_name: 发起调用的节点名称（如 ``"analyze"``）。
            usage: token 用量，格式 ``{"prompt_tokens": int, "completion_tokens": int}``。
            model: 模型名称（可选，用于报告分组）。

        Returns:
            本次调用的 CostRecord。

        Raises:
            KeyError: usage 缺少必需字段。
        """
        prompt_tokens = int(usage["prompt_tokens"])
        completion_tokens = int(usage.get("completion_tokens", 0))

        cost_yuan = (
            prompt_tokens * self.input_price_per_million / 1_000_000
            + completion_tokens * self.output_price_per_million / 1_000_000
        )

        record = CostRecord(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            node_name=node_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_yuan=cost_yuan,
            model=model,
        )

        self._records.append(record)
        self._total_prompt_tokens += prompt_tokens
        self._total_completion_tokens += completion_tokens
        self._total_cost_yuan += cost_yuan

        logger.info(
            "[CostGuard] %s | prompt=%d completion=%d | cost=%.6f yuan | total=%.6f yuan",
            node_name,
            prompt_tokens,
            completion_tokens,
            cost_yuan,
            self._total_cost_yuan,
        )

        return record

    # ------------------------------------------------------------------
    # 检查
    # ------------------------------------------------------------------

    def check(self) -> dict:
        """检查预算状态。

        三重判定：
        - 累计成本 < alert_threshold * budget → ``status="ok"``
        - 累计成本 >= alert_threshold * budget 且 <= budget → ``status="warning"``
        - 累计成本 > budget → 抛出 ``BudgetExceededError``

        Returns:
            预算状态字典，字段：
            - ``status`` (str): ``"ok"`` / ``"warning"``
            - ``total_cost`` (float): 当前累计成本（元）
            - ``budget`` (float): 预算上限（元）
            - ``usage_ratio`` (float): 成本 / 预算 比率
            - ``message`` (str): 人类可读的状态描述

        Raises:
            BudgetExceededError: 累计成本超过预算。
        """
        total_cost = self._total_cost_yuan
        budget = self.budget_yuan
        usage_ratio = total_cost / budget if budget > 0 else float("inf")

        if total_cost > budget:
            raise BudgetExceededError(total_cost, budget)

        if usage_ratio >= self.alert_threshold:
            message = (
                f"预算预警！已使用 {total_cost:.4f} / {budget:.4f} 元 "
                f"({usage_ratio:.1%})，请关注后续调用。"
            )
            logger.warning("[CostGuard] %s", message)
            return {
                "status": "warning",
                "total_cost": total_cost,
                "budget": budget,
                "usage_ratio": usage_ratio,
                "message": message,
            }

        return {
            "status": "ok",
            "total_cost": total_cost,
            "budget": budget,
            "usage_ratio": usage_ratio,
            "message": f"预算正常：{total_cost:.4f} / {budget:.4f} 元 ({usage_ratio:.1%})",
        }

    # ------------------------------------------------------------------
    # 报告
    # ------------------------------------------------------------------

    def get_report(self) -> dict:
        """生成按节点分组的成本报告。

        Returns:
            报告 dict，字段：
            - ``summary`` (dict): 总概括（total_prompt_tokens, total_completion_tokens,
              total_cost_yuan, total_calls）
            - ``by_node`` (dict): 按 node_name 分组的统计列表，每项含
              node_name / calls / prompt_tokens / completion_tokens /
              cost_yuan / cost_ratio
        """
        by_node: dict[str, dict] = {}

        for rec in self._records:
            node = rec.node_name
            if node not in by_node:
                by_node[node] = {
                    "node_name": node,
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cost_yuan": 0.0,
                }
            entry = by_node[node]
            entry["calls"] += 1
            entry["prompt_tokens"] += rec.prompt_tokens
            entry["completion_tokens"] += rec.completion_tokens
            entry["cost_yuan"] += rec.cost_yuan

        for node_data in by_node.values():
            node_data["cost_ratio"] = round(
                node_data["cost_yuan"] / self._total_cost_yuan
                if self._total_cost_yuan > 0
                else 0,
                4,
            )

        return {
            "summary": {
                "total_prompt_tokens": self._total_prompt_tokens,
                "total_completion_tokens": self._total_completion_tokens,
                "total_cost_yuan": round(self._total_cost_yuan, 6),
                "total_calls": len(self._records),
                "budget_yuan": self.budget_yuan,
                "usage_ratio": round(
                    self._total_cost_yuan / self.budget_yuan if self.budget_yuan > 0 else 0,
                    4,
                ),
            },
            "by_node": sorted(by_node.values(), key=lambda x: x["cost_yuan"], reverse=True),
        }

    def save_report(self, path: str | Path | None = None) -> Path:
        """保存成本报告到 JSON 文件。

        Args:
            path: 输出文件路径。默认写入
                ``knowledge/reports/cost_guard_{timestamp}.json``。

        Returns:
            实际写入的绝对路径。

        Raises:
            OSError: 目录创建或文件写入失败。
        """
        if path is None:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            report_dir = Path(__file__).resolve().parent.parent / "knowledge" / "reports"
            report_dir.mkdir(parents=True, exist_ok=True)
            path = report_dir / f"cost_guard_{ts}.json"
        else:
            path = Path(path)

        report = self.get_report()
        report["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("[CostGuard] 报告已保存: %s", path)
        return path.resolve()


# ---------------------------------------------------------------------------
# 测试入口
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    errors: list[str] = []

    # ── 1. 成本追踪正确 ────────────────────────────────────────────
    print("=" * 60)
    print("测试 1: 成本追踪")
    print("=" * 60)

    guard = CostGuard(
        budget_yuan=1.0,
        alert_threshold=0.8,
        input_price_per_million=1.0,
        output_price_per_million=2.0,
    )

    guard.record("analyze", {"prompt_tokens": 500, "completion_tokens": 200}, model="deepseek-v4")
    guard.record("organize", {"prompt_tokens": 300, "completion_tokens": 150}, model="deepseek-v4")
    guard.record("review", {"prompt_tokens": 800, "completion_tokens": 400}, model="deepseek-v4")

    expected_prompt = 500 + 300 + 800  # = 1600
    expected_completion = 200 + 150 + 400  # = 750
    expected_cost = (
        1600 * 1.0 / 1_000_000 + 750 * 2.0 / 1_000_000
    )  # = 0.0016 + 0.0015 = 0.0031

    if guard._total_prompt_tokens == expected_prompt:
        print(f"  ✓ total_prompt_tokens = {guard._total_prompt_tokens} (expected {expected_prompt})")
    else:
        msg = f"total_prompt_tokens {guard._total_prompt_tokens} != {expected_prompt}"
        errors.append(msg)
        print(f"  ✗ {msg}")

    if guard._total_completion_tokens == expected_completion:
        print(f"  ✓ total_completion_tokens = {guard._total_completion_tokens} (expected {expected_completion})")
    else:
        msg = f"total_completion_tokens {guard._total_completion_tokens} != {expected_completion}"
        errors.append(msg)
        print(f"  ✗ {msg}")

    if abs(guard._total_cost_yuan - expected_cost) < 1e-9:
        print(f"  ✓ total_cost_yuan = {guard._total_cost_yuan:.6f} (expected {expected_cost:.6f})")
    else:
        msg = f"total_cost_yuan {guard._total_cost_yuan:.6f} != {expected_cost:.6f}"
        errors.append(msg)
        print(f"  ✗ {msg}")

    # ── 2. 预警阈值触发 ────────────────────────────────────────────
    print()
    print("=" * 60)
    print("测试 2: 预警阈值 (alert_threshold=0.8)")
    print("=" * 60)

    # 构造一个 guard，budget 很小，让 alert_threshold 容易触及
    guard_warn = CostGuard(
        budget_yuan=0.01,   # 1 分钱
        alert_threshold=0.8,
        input_price_per_million=1.0,
        output_price_per_million=2.0,
    )
    # 单次调用即超过 alert_threshold (0.008)，但不超预算 (0.01)
    guard_warn.record("analyze", {"prompt_tokens": 9000, "completion_tokens": 500})

    try:
        status = guard_warn.check()
    except BudgetExceededError:
        errors.append("预警测试失败：不应该抛出 BudgetExceededError")
        print("  ✗ 预警测试：意外抛出 BudgetExceededError")
    else:
        if status["status"] == "warning":
            print(f"  ✓ status = 'warning' (usage_ratio={status['usage_ratio']:.2%})")
            print(f"  ✓ message: {status['message']}")
        else:
            msg = f"status 应为 'warning'，实际为 '{status['status']}'"
            errors.append(msg)
            print(f"  ✗ {msg}")

    # ── 3. 预算超限检测 ────────────────────────────────────────────
    print()
    print("=" * 60)
    print("测试 3: 预算超限 (BudgetExceededError)")
    print("=" * 60)

    guard_over = CostGuard(
        budget_yuan=0.01,
        alert_threshold=0.8,
        input_price_per_million=1.0,
        output_price_per_million=2.0,
    )
    guard_over.record("analyze", {"prompt_tokens": 10000, "completion_tokens": 5000})

    try:
        guard_over.check()
        errors.append("超限测试失败：应该抛出 BudgetExceededError")
        print("  ✗ 超限测试：未抛出异常")
    except BudgetExceededError as e:
        print(f"  ✓ 正确抛出 BudgetExceededError")
        print(f"  ✓ total_cost={e.total_cost:.6f}, budget={e.budget:.4f}")
        print(f"  ✓ message: {e}")

    # ── 4. 报告生成 ─────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("测试 4: 按节点分组报告")
    print("=" * 60)

    report = guard.get_report()
    summary = report["summary"]
    print(f"  summary: {json.dumps(summary, ensure_ascii=False)}")

    by_node = report["by_node"]
    if len(by_node) == 3:
        print(f"  ✓ by_node 包含 3 个节点:")
        for n in by_node:
            print(f"    - {n['node_name']}: calls={n['calls']}, cost={n['cost_yuan']:.6f}")
    else:
        msg = f"by_node 应有 3 个分组，实际 {len(by_node)} 个"
        errors.append(msg)
        print(f"  ✗ {msg}")

    # ── 汇总 ────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    if errors:
        print(f"✗ 共 {len(errors)} 个失败：")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("✓ 全部测试通过 (4/4)")
        sys.exit(0)
