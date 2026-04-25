"""Pipeline 成本报告集成测试。"""

from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

# 添加 pipeline/ 目录到 sys.path 以支持 from model_client import ...
_pipeline_dir = str(Path(__file__).resolve().parent.parent / "pipeline")
if _pipeline_dir not in sys.path:
    sys.path.insert(0, _pipeline_dir)

from model_client import Usage, tracker
from pipeline import Pipeline


def _capture_log() -> tuple[io.StringIO, logging.Handler]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return stream, handler


def _cleanup_handler(handler: logging.Handler) -> None:
    logging.getLogger().removeHandler(handler)


def test_print_cost_report() -> None:
    tracker.clear()
    tracker.record(Usage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500), "deepseek")

    pipe = Pipeline(sources=["github"], limit=5, dry_run=True)
    pipe._stats.update(collected=10, analyzed=8, organized=6, saved=6)

    stream, handler = _capture_log()
    pipe._print_cost_report()
    _cleanup_handler(handler)
    output = stream.getvalue()

    assert "deepseek" in output
    assert "calls=1" in output
    assert "collected=10" in output
    assert "analyzed=8" in output
    assert "organized=6" in output
    assert "saved=6" in output
    assert "¥" in output
    assert "avg_cost_per_article" in output


def test_print_cost_report_no_records() -> None:
    tracker.clear()

    pipe = Pipeline(sources=["rss"], limit=3, dry_run=True)
    pipe._stats.update(collected=0, analyzed=0, organized=0, saved=0)

    stream, handler = _capture_log()
    pipe._print_cost_report()
    _cleanup_handler(handler)
    output = stream.getvalue()

    assert "No cost records" in output
    assert "saved=0" in output
    assert "avg_cost_per_article=N/A" in output


def test_print_cost_report_multiple_providers() -> None:
    tracker.clear()
    tracker.record(Usage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500), "deepseek")
    tracker.record(Usage(prompt_tokens=500, completion_tokens=300, total_tokens=800), "qwen")

    pipe = Pipeline(sources=["github", "rss"], limit=10, dry_run=True)
    pipe._stats.update(collected=15, analyzed=12, organized=10, saved=10)

    stream, handler = _capture_log()
    pipe._print_cost_report()
    _cleanup_handler(handler)
    output = stream.getvalue()

    assert "deepseek" in output
    assert "qwen" in output
    assert "collected=15" in output


def test_print_cost_report_saved_zero() -> None:
    tracker.clear()
    tracker.record(Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150), "deepseek")

    pipe = Pipeline(sources=["github"], limit=5, dry_run=True)
    pipe._stats.update(collected=10, analyzed=8, organized=6, saved=0)

    stream, handler = _capture_log()
    pipe._print_cost_report()
    _cleanup_handler(handler)
    output = stream.getvalue()

    assert "saved=0" in output
    assert "avg_cost_per_article=N/A" in output


if __name__ == "__main__":
    test_print_cost_report()
    test_print_cost_report_no_records()
    test_print_cost_report_multiple_providers()
    test_print_cost_report_saved_zero()
    print("All pipeline report tests passed!")
