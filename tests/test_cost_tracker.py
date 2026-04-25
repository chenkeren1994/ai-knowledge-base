"""CostTracker 单元测试。"""

from __future__ import annotations

import io
import logging

from pipeline.model_client import CostTracker, LLMResponse, Usage, tracker


def make_usage(prompt: int = 0, completion: int = 0) -> Usage:
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )


def test_record_and_cost() -> None:
    tracker = CostTracker()

    tracker.record(make_usage(100, 50), "deepseek")
    # deepseek: (100/1M)*1 + (50/1M)*2 = 0.0001 + 0.0001 = 0.0002
    assert tracker.estimated_cost("deepseek") == 0.0002

    tracker.record(make_usage(200, 100), "deepseek")
    # total: (300/1M)*1 + (150/1M)*2 = 0.0003 + 0.0003 = 0.0006
    assert tracker.estimated_cost("deepseek") == 0.0006


def test_qwen_cost() -> None:
    tracker = CostTracker()
    tracker.record(make_usage(500, 200), "qwen")
    # qwen: (500/1M)*4 + (200/1M)*12 = 0.002 + 0.0024 = 0.0044
    assert tracker.estimated_cost("qwen") == 0.0044


def test_openai_cost() -> None:
    tracker = CostTracker()
    tracker.record(make_usage(1000, 500), "openai")
    # openai: (1000/1M)*150 + (500/1M)*600 = 0.15 + 0.30 = 0.45
    assert tracker.estimated_cost("openai") == 0.45


def test_unknown_provider() -> None:
    tracker = CostTracker()
    tracker.record(make_usage(100, 50), "unknown_provider")
    assert tracker.estimated_cost("unknown_provider") == 0.0


def test_empty_tracker() -> None:
    tracker = CostTracker()
    assert tracker.estimated_cost("deepseek") == 0.0
    assert tracker.estimated_cost("qwen") == 0.0
    assert tracker.estimated_cost("openai") == 0.0


def test_multiple_providers() -> None:
    tracker = CostTracker()
    tracker.record(make_usage(100, 50), "deepseek")
    tracker.record(make_usage(200, 100), "qwen")
    tracker.record(make_usage(300, 150), "openai")

    assert tracker.estimated_cost("deepseek") == 0.0002
    assert tracker.estimated_cost("qwen") == 0.0020
    assert tracker.estimated_cost("openai") == 0.1350


def test_report_output() -> None:
    tracker = CostTracker()
    tracker.record(make_usage(100, 50), "deepseek")
    tracker.record(make_usage(200, 100), "qwen")

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("pipeline.model_client")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    tracker.report()
    logger.removeHandler(handler)
    output = stream.getvalue()

    assert "deepseek" in output
    assert "qwen" in output
    assert "¥" in output


def test_report_filtered() -> None:
    tracker = CostTracker()
    tracker.record(make_usage(100, 50), "deepseek")
    tracker.record(make_usage(200, 100), "qwen")

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("pipeline.model_client")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    tracker.report(provider="deepseek")
    logger.removeHandler(handler)
    output = stream.getvalue()

    assert "deepseek" in output
    assert "qwen" not in output


def test_report_empty() -> None:
    tracker = CostTracker()

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("pipeline.model_client")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    tracker.report()
    logger.removeHandler(handler)
    output = stream.getvalue()

    assert "No cost records" in output


def test_large_values() -> None:
    tracker = CostTracker()
    tracker.record(make_usage(1_000_000, 500_000), "deepseek")
    # (1M/1M)*1 + (500k/1M)*2 = 1.0 + 1.0 = 2.0
    assert tracker.estimated_cost("deepseek") == 2.0


# ---------------------------------------------------------------------------
# chat_with_retry 自动记录测试
# ---------------------------------------------------------------------------


FakeResponse = LLMResponse(
    content="mock response",
    usage=Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
    model="deepseek-chat",
)


class _MockProvider:
    """模拟 LLMProvider，返回固定响应。"""

    def __init__(self, name: str = "deepseek") -> None:
        self.name = name

    async def chat(
        self,
        messages: list[dict[str, str]] | None = None,
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        return FakeResponse

    async def close(self) -> None:
        pass


async def test_chat_with_retry_records_usage() -> None:
    from pipeline.model_client import chat_with_retry

    tracker.clear()
    provider = _MockProvider(name="deepseek")

    response = await chat_with_retry(
        provider=provider,  # type: ignore
        messages=[{"role": "user", "content": "hello"}],
        max_retries=0,
    )

    assert len(tracker.records) == 1
    rec = tracker.records[0]
    assert rec.provider == "deepseek"
    assert rec.prompt_tokens == 100
    assert rec.completion_tokens == 50
    assert rec.total_tokens == 150


async def test_chat_with_retry_multiple_calls() -> None:
    from pipeline.model_client import chat_with_retry

    tracker.clear()

    for _ in range(3):
        await chat_with_retry(
            provider=_MockProvider(name="deepseek"),  # type: ignore
            messages=[{"role": "user", "content": "hi"}],
            max_retries=0,
        )

    assert len(tracker.records) == 3
    assert all(r.provider == "deepseek" for r in tracker.records)


async def test_chat_with_retry_different_providers() -> None:
    from pipeline.model_client import chat_with_retry

    tracker.clear()

    await chat_with_retry(
        provider=_MockProvider(name="deepseek"),  # type: ignore
        messages=[{"role": "user", "content": "hi"}],
        max_retries=0,
    )
    await chat_with_retry(
        provider=_MockProvider(name="qwen"),  # type: ignore
        messages=[{"role": "user", "content": "hi"}],
        max_retries=0,
    )

    assert len(tracker.records) == 2
    assert tracker.records[0].provider == "deepseek"
    assert tracker.records[1].provider == "qwen"


async def test_chat_with_retry_failure_not_recorded() -> None:
    from pipeline.model_client import chat_with_retry

    tracker.clear()

    class _FailingProvider:
        name = "deepseek"

        async def chat(self, **kwargs) -> None:
            request = httpx.Request("POST", "https://api.example.com/chat")
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError("API error", request=request, response=response)

    try:
        await chat_with_retry(
            provider=_FailingProvider(),  # type: ignore
            messages=[{"role": "user", "content": "hi"}],
            max_retries=0,
        )
    except RuntimeError:
        pass

    assert len(tracker.records) == 0


if __name__ == "__main__":
    import httpx

    test_record_and_cost()
    test_qwen_cost()
    test_openai_cost()
    test_unknown_provider()
    test_empty_tracker()
    test_multiple_providers()
    test_report_output()
    test_report_filtered()
    test_report_empty()
    test_large_values()

    import asyncio
    asyncio.run(test_chat_with_retry_records_usage())
    asyncio.run(test_chat_with_retry_multiple_calls())
    asyncio.run(test_chat_with_retry_different_providers())
    asyncio.run(test_chat_with_retry_failure_not_recorded())

    print("All tests passed!")
