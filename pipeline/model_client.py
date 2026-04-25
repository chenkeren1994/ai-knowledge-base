#!/usr/bin/env python3
"""统一 LLM 调用客户端模块。

通过环境变量切换模型提供商，使用 httpx 直接调用 OpenAI 兼容 API，
支持 DeepSeek / Qwen / OpenAI 三种后端，提供重试、用量统计和成本估算。

环境变量：
    LLM_PROVIDER: 提供商，枚举 deepseek / qwen / openai，默认 deepseek
    DEEPSEEK_API_KEY: DeepSeek API 密钥
    QWEN_API_KEY: Qwen (DashScope) API 密钥
    OPENAI_API_KEY: OpenAI API 密钥

用法示例：
    import asyncio
    from pipeline.model_client import quick_chat

    response = asyncio.run(quick_chat("用一句话介绍 Python"))
    print(response.content)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 国产模型价格表（元 / 百万 tokens）
# ---------------------------------------------------------------------------
_RMB_PRICING: dict[str, dict[str, float]] = {
    "deepseek": {"input": 1, "output": 2},
    "qwen": {"input": 4, "output": 12},
    "openai": {"input": 150, "output": 600},
}


@dataclass
class _CostRecord:
    """单次 LLM 调用的成本记录。"""

    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class CostTracker:
    """LLM 调用成本追踪器，支持国产模型定价。"""

    def __init__(self) -> None:
        self._records: list[_CostRecord] = []

    def record(self, usage: Usage, provider: str) -> None:
        """记录一次 LLM 调用的用量。

        Args:
            usage: Usage 实例，含 prompt_tokens / completion_tokens / total_tokens。
            provider: 提供商名称，如 ``deepseek``、``qwen``、``openai``。
        """
        self._records.append(_CostRecord(
            provider=provider,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        ))

    def estimated_cost(self, provider: str) -> float:
        """估算指定提供商的历史总成本（元）。

        Args:
            provider: 提供商名称。

        Returns:
            累计估算成本（元），保留 4 位小数。
        """
        pricing = _RMB_PRICING.get(provider)
        if pricing is None:
            return 0.0

        total_prompt = 0
        total_completion = 0
        for rec in self._records:
            if rec.provider == provider:
                total_prompt += rec.prompt_tokens
                total_completion += rec.completion_tokens

        cost = (total_prompt / 1_000_000) * pricing["input"]
        cost += (total_completion / 1_000_000) * pricing["output"]
        return round(cost, 4)

    @property
    def records(self) -> list[_CostRecord]:
        """返回所有记录的只读副本。"""
        return list(self._records)

    def clear(self) -> None:
        """清除所有记录（主要用于测试）。"""
        self._records.clear()

    def report(self, provider: str = "") -> None:
        """打印成本报告。

        Args:
            provider: 提供商名称，为空则打印所有提供商。
        """
        providers = [provider] if provider else sorted({
            rec.provider for rec in self._records
        })
        any_data = False
        for prov in providers:
            records = [r for r in self._records if r.provider == prov]
            if not records:
                continue
            any_data = True
            total_pt = sum(r.prompt_tokens for r in records)
            total_ct = sum(r.completion_tokens for r in records)
            total_tt = sum(r.total_tokens for r in records)
            cost = self.estimated_cost(prov)
            logger.info(
                "Provider: %s | calls=%d | prompt=%d | completion=%d | total=%d | cost=¥%.4f",
                prov, len(records), total_pt, total_ct, total_tt, cost,
            )
        if not any_data:
            logger.info("No cost records available.")

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    """LLM 调用用量统计。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    """LLM 统一返回结构。"""

    content: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    finish_reason: str = "stop"


# ---------------------------------------------------------------------------
# Token 估算与成本计算
# ---------------------------------------------------------------------------

# 按模型分组的字符/Token 比率（中文 ≈ 1.5 字符/Token，英文 ≈ 4 字符/Token）
_TOKEN_CHARS_PER_TOKEN: dict[str, float] = {
    "deepseek-chat": 2.5,
    "deepseek-reasoner": 2.5,
    "qwen-turbo": 2.2,
    "qwen-plus": 2.2,
    "qwen-max": 2.2,
    "gpt-4o": 3.5,
    "gpt-4o-mini": 3.5,
}
_DEFAULT_CHARS_PER_TOKEN = 3.0

# 定价表（USD / 百万 Token）
_PRICING: dict[str, dict[str, float]] = {
    "deepseek-chat": {"input": 0.27, "output": 1.10},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    "qwen-turbo": {"input": 0.042, "output": 0.084},
    "qwen-plus": {"input": 0.11, "output": 0.28},
    "qwen-max": {"input": 0.28, "output": 0.84},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}


def estimate_tokens(text: str, model: str = "") -> int:
    """基于字符数粗略估算 Token 数量。

    Args:
        text: 输入文本。
        model: 模型名称，用于选择字符/Token 比率。

    Returns:
        估算的 Token 数量。
    """
    ratio = _TOKEN_CHARS_PER_TOKEN.get(model, _DEFAULT_CHARS_PER_TOKEN)
    return max(1, int(len(text) / ratio))


def estimate_cost(
    prompt_tokens: int,
    completion_tokens: int,
    model: str = "",
) -> float:
    """估算单次 LLM 调用的费用（USD）。

    Args:
        prompt_tokens: 输入 Token 数。
        completion_tokens: 输出 Token 数。
        model: 模型名称。

    Returns:
        估算费用（美元）。
    """
    pricing = _PRICING.get(model)
    if pricing is None:
        return 0.0
    input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
    output_cost = (completion_tokens / 1_000_000) * pricing["output"]
    return round(input_cost + output_cost, 6)


# ---------------------------------------------------------------------------
# 抽象基类与实现
# ---------------------------------------------------------------------------

# 各提供商的默认配置
_PROVIDER_CONFIGS: dict[str, dict[str, str]] = {
    "deepseek": {
        "api_base": "https://api.deepseek.com/v1",
        "default_model": "deepseek-v4-flash",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "env_key": "QWEN_API_KEY",
    },
    "openai": {
        "api_base": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "env_key": "OPENAI_API_KEY",
    },
}


class LLMProvider(ABC):
    """LLM 提供商的抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """提供商名称，如 ``deepseek``、``qwen``、``openai``。"""
        ...

    @property
    @abstractmethod
    def default_model(self) -> str:
        """默认模型名称。"""
        ...

    @property
    @abstractmethod
    def api_base(self) -> str:
        """API 基础 URL。"""
        ...

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """发送对话请求并返回统一响应。

        Args:
            messages: 消息列表，格式 ``[{"role": "...", "content": "..."}]``。
            model: 模型名称，为空则使用默认模型。
            temperature: 采样温度。
            max_tokens: 最大输出 Token 数。

        Returns:
            LLMResponse 实例。
        """
        ...


class OpenAICompatibleProvider(LLMProvider):
    """基于 OpenAI 兼容 API 的通用实现，使用 httpx 直接调用。"""

    def __init__(
        self,
        name: str,
        api_base: str,
        api_key: str,
        default_model: str,
        timeout: float = 60.0,
    ) -> None:
        """初始化提供商。

        Args:
            name: 提供商名称。
            api_base: API 基础 URL。
            api_key: API 密钥。
            default_model: 默认模型。
            timeout: 请求超时秒数。
        """
        self._name = name
        self._api_base = api_base.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def default_model(self) -> str:
        return self._default_model

    @property
    def api_base(self) -> str:
        return self._api_base

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 httpx 异步客户端（延迟初始化）。"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._api_base,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self._timeout),
            )
        return self._client

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """发送对话请求（OpenAI 兼容格式）。"""
        client = await self._get_client()
        payload = {
            "model": model or self._default_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        logger.debug("Sending chat request to %s: model=%s", self._name, payload["model"])
        response = await client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        return self._parse_response(data, payload["model"])

    @staticmethod
    def _parse_response(data: dict, model: str) -> LLMResponse:
        """将 API 原始 JSON 解析为 LLMResponse。"""
        choice = data["choices"][0]
        usage_raw = data.get("usage", {})
        usage = Usage(
            prompt_tokens=usage_raw.get("prompt_tokens", 0),
            completion_tokens=usage_raw.get("completion_tokens", 0),
            total_tokens=usage_raw.get("total_tokens", 0),
        )
        return LLMResponse(
            content=choice["message"]["content"].strip(),
            usage=usage,
            model=model,
            finish_reason=choice.get("finish_reason", "stop"),
        )

    async def close(self) -> None:
        """关闭底层 HTTP 客户端。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ---------------------------------------------------------------------------
# 提供商工厂
# ---------------------------------------------------------------------------

_default_provider: Optional[LLMProvider] = None


def _resolve_provider_name() -> str:
    """从环境变量 LLM_PROVIDER 解析提供商名称。"""
    name = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()
    if name not in _PROVIDER_CONFIGS:
        raise ValueError(
            f"不支持的 LLM 提供商: {name!r}，"
            f"可用值: {sorted(_PROVIDER_CONFIGS)}"
        )
    return name


def build_provider(provider_name: str = "") -> LLMProvider:
    """根据提供商名称构造 LLMProvider 实例。

    Args:
        provider_name: 提供商名称，为空则从 LLM_PROVIDER 环境变量读取。

    Returns:
        LLMProvider 实例。

    Raises:
        ValueError: 提供商不支持或 API 密钥未设置。
    """
    name = provider_name or _resolve_provider_name()
    config = _PROVIDER_CONFIGS[name]
    api_key = os.getenv(config["env_key"], "")
    if not api_key:
        raise ValueError(
            f"环境变量 {config['env_key']} 未设置，无法初始化 {name} 提供商"
        )
    return OpenAICompatibleProvider(
        name=name,
        api_base=config["api_base"],
        api_key=api_key,
        default_model=config["default_model"],
    )


def get_provider() -> LLMProvider:
    """获取全局默认 LLMProvider（单例延迟初始化）。

    Returns:
        全局唯一的 LLMProvider 实例。
    """
    global _default_provider
    if _default_provider is None:
        _default_provider = build_provider()
    return _default_provider


create_provider = build_provider


def reset_provider() -> None:
    """重置全局提供商实例（用于测试或切换提供商）。"""
    global _default_provider
    _default_provider = None


# ---------------------------------------------------------------------------
# 全局成本追踪器
# ---------------------------------------------------------------------------

tracker = CostTracker()


# ---------------------------------------------------------------------------
# 带重试的对话函数
# ---------------------------------------------------------------------------


async def chat_with_retry(
    provider: Optional[LLMProvider] = None,
    messages: Optional[list[dict[str, str]]] = None,
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    max_retries: int = 3,
) -> LLMResponse:
    """带指数退避重试的 LLM 对话调用。

    失败时最多重试 max_retries 次，每次等待 2^n 秒。

    Args:
        provider: LLMProvider 实例，为空则使用全局默认。
        messages: 消息列表。
        model: 模型名称。
        temperature: 采样温度。
        max_tokens: 最大输出 Token 数。
        max_retries: 最大重试次数。

    Returns:
        LLMResponse 实例。

    Raises:
        RuntimeError: 所有重试均失败。
    """
    if provider is None:
        provider = get_provider()
    if messages is None:
        raise ValueError("messages 不能为空")

    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            start = time.monotonic()
            result = await provider.chat(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            elapsed = time.monotonic() - start
            tracker.record(result.usage, provider.name)
            logger.info(
                "chat_with_retry 成功 (attempt=%d/%d, elapsed=%.2fs, tokens=%d)",
                attempt + 1,
                max_retries + 1,
                elapsed,
                result.usage.total_tokens,
            )
            return result
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_error = exc
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(
                    "chat_with_retry 失败 (attempt=%d/%d, wait=%ds): %s",
                    attempt + 1,
                    max_retries + 1,
                    wait,
                    exc,
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    "chat_with_retry 全部重试失败 (attempts=%d): %s",
                    max_retries + 1,
                    exc,
                )

    raise RuntimeError(
        f"LLM 调用失败，已重试 {max_retries} 次: {last_error}"
    )


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


async def quick_chat(
    prompt: str,
    model: str = "",
    system_prompt: str = "",
    provider: Optional[LLMProvider] = None,
) -> LLMResponse:
    """一句话调用 LLM，适合简单问答场景。

    Args:
        prompt: 用户输入的提示词。
        model: 模型名称，为空则使用默认模型。
        system_prompt: 可选的系统提示。
        provider: LLMProvider 实例，为空则使用全局默认。

    Returns:
        LLMResponse 实例。
    """
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    return await chat_with_retry(
        provider=provider,
        messages=messages,
        model=model,
    )


# ---------------------------------------------------------------------------
# 测试入口
# ---------------------------------------------------------------------------


async def _main() -> None:
    """测试运行入口，通过环境变量配置后执行真实 LLM 调用。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # ---------- 1. 显示当前配置 ----------
    provider_name = _resolve_provider_name()
    config = _PROVIDER_CONFIGS[provider_name]
    api_key_set = bool(os.getenv(config["env_key"], ""))
    logger.info("LLM_PROVIDER = %s, API Key set = %s", provider_name, api_key_set)

    if not api_key_set:
        logger.error(
            "环境变量 %s 未设置，跳过实际调用。"
            "请设置后重新运行测试。",
            config["env_key"],
        )
        return

    # ---------- 2. 构造提供商 ----------
    logger.info("构建 %s 提供商...", provider_name)
    provider = build_provider()
    logger.info(
        "提供商就绪: name=%s, default_model=%s, api_base=%s",
        provider.name,
        provider.default_model,
        provider.api_base,
    )

    # ---------- 3. Token 估算与成本测试 ----------
    sample_text = "Python is a high-level programming language used for AI development."
    est_tokens = estimate_tokens(sample_text, provider.default_model)
    est_cost = estimate_cost(1000, 500, provider.default_model)
    logger.info("Token 估算: text_len=%d -> ~%d tokens", len(sample_text), est_tokens)
    logger.info("成本估算: 1000 prompt + 500 completion -> $%.6f", est_cost)

    # ---------- 4. 真实 LLM 调用 ----------
    logger.info("发送测试调用: '用一句话介绍什么是 RAG' ...")
    try:
        response = await quick_chat("用一句话介绍什么是 RAG")
        logger.info("LLM 响应: %s", response.content)
        logger.info(
            "用量统计: prompt=%d, completion=%d, total=%d, model=%s",
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
            response.usage.total_tokens,
            response.model,
        )
        actual_cost = estimate_cost(
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
            response.model,
        )
        logger.info("本次实际费用: $%.6f", actual_cost)
    except Exception as exc:
        logger.exception("LLM 调用失败: %s", exc)

    # ---------- 5. 带重试的调用测试 ----------
    logger.info("测试 retry_chat (带重试)...")
    try:
        response = await chat_with_retry(
            provider=provider,
            messages=[{"role": "user", "content": "Hello, 1+1=?"}],
            max_retries=2,
        )
        logger.info("retry_chat 响应: %s", response.content)
    except Exception as exc:
        logger.exception("retry_chat 失败: %s", exc)

    # ---------- 6. 清理 ----------
    if hasattr(provider, "close"):
        await provider.close()
    logger.info("测试完成")


if __name__ == "__main__":
    asyncio.run(_main())
