"""BaseAdapter: shared adapter base class (PR-3 model-agnostic).

Design:
  - Unified error handling, HTTP client management, token estimation
  - Reduces duplicate code across adapters
  - Optional inheritance: adapters can skip BaseAdapter and implement
    the ModelAdapter Protocol directly

BaseAdapter provides:
  - make_error_response(status_code, body) -> ModelResponse
  - with_retry(fn, max_retries=3) -> ModelResponse (exponential backoff)
  - estimate_tokens(messages) -> int (rough estimate)
  - close() (close HTTP client)

IPR constraints:
  IPR-3: stdlib + common HTTP libs only, no model SDK
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from typing import Any, ClassVar

import httpx

from zall.core.model import ModelResponse, StopReason
from zall.safety.config import load_config

# Common HTTP error codes -> user-friendly messages
_ERROR_MAP: dict[int, str] = {
    401: (
        "API authentication failed. Check your API key:\n"
        "  - Set ZALL_API_KEY environment variable, or\n"
        "  - Edit ~/.zall/config.toml and add your key under [auth]\n"
        "  - Run /doctor to check current config"
    ),
    402: (
        "API quota exhausted (HTTP 402 payment required). "
        "Top up your account balance, or switch provider/model with /model."
    ),
    403: (
        "API access denied. Your API key may not have permission "
        "for this model or endpoint. Try /model to switch models."
    ),
    404: (
        "API endpoint not found. Check your api_base setting."
    ),
    422: (
        "API request was rejected as invalid (HTTP 422). "
        "This usually means a tool schema issue — check your tool definitions "
        "and try again. Run /doctor for config diagnostics."
    ),
    429: (
        "API rate limit exceeded. Wait a moment and try again, "
        "or switch to a different model with /model."
    ),
    500: (
        "API server error. The model provider is experiencing issues. "
        "Try again later or switch models with /model."
    ),
    502: (
        "API gateway error. The model provider's upstream service is down. "
        "Try again later."
    ),
    503: (
        "API service unavailable. The model provider is under maintenance. "
        "Try again later."
    ),
}


# 重试原因标签 (单一真相源, REPL/TUI 共用) — "retrying (n/N) in Xs: <原因>"
RETRY_REASON: dict[str, str] = {
    "transport": "network error",
    "api": "rate limited / server error",
    "semantic": "empty response",
    # 流式零产出失败 → 降级非流式重试 (core/loop.py 发出, 2026-07-26)
    "stream_fallback": "stream failed, retrying non-streaming",
}


# ═══════════════════════════════════════════════════════════════════
# RetryBudget — 区分预算的重试策略 (v0.5.0, Grok Build 启发)
# ═══════════════════════════════════════════════════════════════════


class RetryBudget:
    """区分预算的 API 重试策略。

    三种预算独立计数:
      - transport: 网络层错误 (连接断开、超时、协议错误)
      - api:       API 层错误 (429 限流、5xx 服务器错误)
      - semantic:  语义层错误 (模型空回复、截断、无效内容)

    Grok Build 启发: 不同错误类型需要不同的重试策略。
    网络抖动可以多试几次, 但持续 429 需要更长的退避。
    """

    __test__ = False

    # 错误分类常量 (v0.6.0: 借鉴 Claude Code 错误分类)
    RATE_LIMIT = "rate_limit"        # 429
    SERVER_ERROR = "server_error"    # 500/502/503/529
    TIMEOUT = "timeout"              # 连接超时/读取超时
    AUTH_FAILED = "auth_failed"      # 401/403 — 不重试
    INVALID_REQUEST = "invalid_request"  # 400/422 — 不重试
    CONTENT_FILTER = "content_filter"    # 内容被截断 — 不重试
    TRANSPORT = "transport"          # 网络层错误
    SEMANTIC = "semantic"            # 语义层错误

    def __init__(
        self,
        max_transport: int = 3,
        max_api: int = 5,
        max_semantic: int = 2,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        jitter: float = 0.25,
        # v0.6.0: 用户可见的重试回调 (借鉴 Claude Code)
        on_retry: Callable[[str, float, int, int], None] | None = None,
    ) -> None:
        self.max_transport = max_transport
        self.max_api = max_api
        self.max_semantic = max_semantic
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter
        self._counts: dict[str, int] = {"transport": 0, "api": 0, "semantic": 0}
        self._on_retry = on_retry

    @property
    def total(self) -> int:
        return sum(self._counts.values())

    def reset(self) -> None:
        """重置所有预算计数器 (会话开始/恢复时调用)。"""
        self._counts = {"transport": 0, "api": 0, "semantic": 0}

    def set_callback(self, on_retry: Callable[[str, float, int, int], None] | None) -> None:
        """运行期挂接重试通知回调 (构造后由 CLI/loop 注入)。"""
        self._on_retry = on_retry

    def can_retry(self, category: str = "transport") -> bool:
        """检查指定类别是否还有重试预算。"""
        max_budget = {
            "transport": self.max_transport,
            "api": self.max_api,
            "semantic": self.max_semantic,
        }
        return self._counts.get(category, 0) < max_budget.get(category, 0)

    def record_attempt(
        self, category: str = "transport", delay_override: float | None = None,
    ) -> float:
        """记录一次重试, 返回退避延迟秒数 (带 jitter)。

        delay_override: 服务器指定的延迟 (如 Retry-After 头), 优先于指数退避。
        无论是否 override, 预算计数都必须消耗 (2026-07-26 bugfix: 此前
        Retry-After 路径跳过 record_attempt 导致持续 429 时无限重试)。
        """
        self._counts[category] = self._counts.get(category, 0) + 1
        if delay_override is not None:
            delay = min(max(delay_override, 0.0), self.max_delay)
        else:
            delay = min(
                self.base_delay * (2 ** (self._counts[category] - 1)),
                self.max_delay,
            )
            if self.jitter > 0:
                delay *= 1.0 + random.uniform(-self.jitter, self.jitter)
        self._notify_retry(category, delay, self._counts[category],
                           self.max_transport if category == "transport" else
                           self.max_api if category == "api" else
                           self.max_semantic)
        return delay

    def get_summary(self) -> dict[str, int]:
        """返回当前预算使用情况摘要。"""
        return dict(self._counts)

    @staticmethod
    def classify_error(error: Exception) -> str:
        """将异常分类为 transport / api / semantic。"""
        if isinstance(error, (httpx.ConnectError, httpx.TimeoutException,
                              httpx.RemoteProtocolError, httpx.ReadError,
                              ConnectionError, TimeoutError, OSError)):
            return "transport"
        # httpx.HTTPStatusError wraps non-2xx responses
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code if hasattr(error, 'response') else 0
            if status in (429,) or (500 <= status < 600):
                return "api"
            return "api"  # 400-level errors are also "api" but not retryable
        return "semantic"

    @staticmethod
    def is_retryable_http(status_code: int) -> bool:
        """判断 HTTP 状态码是否可重试。"""
        return status_code == 429 or (500 <= status_code < 600)

    @staticmethod
    def classify_http_status(status_code: int) -> str:
        """将 HTTP 状态码分类为错误类型 (v0.6.0)。

        Returns:
            One of RATE_LIMIT, SERVER_ERROR, AUTH_FAILED, INVALID_REQUEST, TRANSPORT
        """
        if status_code == 429:
            return RetryBudget.RATE_LIMIT
        if status_code == 529:
            return RetryBudget.SERVER_ERROR
        if 500 <= status_code < 600:
            return RetryBudget.SERVER_ERROR
        if status_code in (401, 403):
            return RetryBudget.AUTH_FAILED
        if status_code in (400, 402, 422):
            return RetryBudget.INVALID_REQUEST
        return RetryBudget.TRANSPORT

    @staticmethod
    def is_retryable_status(status_code: int) -> bool:
        """判断 HTTP 状态码是否可重试 (v0.6.0)。

        不重试: 401/403/400/422 (客户端错误, 重试也无效)
        重试: 429, 5xx, 529 (临时错误)
        """
        return status_code == 429 or (500 <= status_code < 600)

    def _notify_retry(self, category: str, delay: float, attempt: int, max_retries: int) -> None:
        """通知重试回调 (如果设置了)。"""
        if self._on_retry is not None:
            try:
                self._on_retry(category, delay, attempt, max_retries)
            except Exception:
                pass


class BaseAdapter:
    """Shared adapter base class — unified error handling, HTTP client, token estimation.

    Subclass usage:
        super().__init__(api_key, api_base, model, timeout)
        self._client = httpx.Client(...)  # or custom client
    """

    __test__ = False

    # Retryable exception types (network jitter, timeout).
    RETRYABLE_EXC: ClassVar[tuple[Any, ...]] = (
        httpx.ConnectError, httpx.TimeoutException,
        httpx.ReadError, httpx.RemoteProtocolError,
        ConnectionError, TimeoutError, OSError,
    )
    # Non-retryable exception types (programming errors).
    NON_RETRYABLE_EXC: ClassVar[tuple[Any, ...]] = (
        ValueError, TypeError, KeyError, AttributeError,
        json.JSONDecodeError,
    )

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        cfg = load_config()
        self._api_key = api_key or cfg["api_key"]
        self._api_base = api_base or cfg["api_base"]
        self._model = model or cfg["model"]
        self._timeout = timeout
        # 重试可见性: CLI/loop 经 set_retry_callback 注入, 静默退避期间通知 UI。
        self._retry_callback: Callable[[str, float, int, int], None] | None = None
        # Warn on non-HTTPS API base URLs.
        if self._api_base and not self._api_base.startswith("https://"):
            import sys
            print(f"  ⚠ WARNING: API base URL is not HTTPS: {self._api_base}",
                  file=sys.stderr)
        # Subclasses should create their own HTTP client.

    def close(self) -> None:
        """Close the HTTP client. Subclasses should override."""

    def set_retry_callback(
        self, cb: Callable[[str, float, int, int], None] | None,
    ) -> None:
        """注入重试通知回调 (category, delay, attempt, max_attempts)。

        loop 层 duck-typed 调用 (core 不 import adapters, IPR-3)。
        """
        self._retry_callback = cb

    def _dispatch_retry(
        self, category: str, delay: float, attempt: int, max_attempts: int,
    ) -> None:
        """转发重试通知; 回调异常吞掉, 不阻断重试路径。"""
        cb = self._retry_callback
        if cb is not None:
            try:
                cb(category, delay, attempt, max_attempts)
            except Exception:
                pass

    @property
    def model_name(self) -> str:
        return self._model

    # ── Error handling ──

    def make_error_response(self, status_code: int, body: str, raw: dict[str, Any] | None = None) -> ModelResponse:
        """Build a user-friendly error ModelResponse.

        Maps common HTTP error codes to readable hints, avoiding raw JSON exposure.
        404 + "model is not found" 上游语义 (实测 sensenova: api_base 正确但模型 id
        过期) 时指向模型切换, 而非误导用户去查 api_base。
        """
        hint = _ERROR_MAP.get(
            status_code,
            f"API error (HTTP {status_code}). Check your config with /doctor.",
        )
        if status_code == 404 and "model" in body.lower() and "not found" in body.lower():
            hint = (
                "Model not found on this endpoint (api_base is reachable, the model "
                "id is not). Run /model to pick a valid model, or check the "
                "provider's model list."
            )
        error_raw = {"status": status_code, "body": body[:500]}
        if raw:
            error_raw.update(raw)
        return ModelResponse(
            content=f"[{hint}]",
            stop_reason=StopReason.STOP,
            raw=error_raw,
        )

    # ── Retry ──

    @staticmethod
    def _backoff_delay(attempt: int, base_delay: float = 1.0, max_delay: float = 60.0) -> float:
        """Exponential backoff with jitter (+/- 25%).

        Formula:
          delay = min(base_delay * 2^attempt, max_delay) * uniform(0.75, 1.25)

        Jitter prevents thundering herd when multiple requests fail simultaneously.
        Cap at max_delay prevents unbounded wait.
        """
        delay = min(base_delay * (2 ** attempt), max_delay)
        jitter = random.uniform(0.75, 1.25)
        return delay * jitter

    def with_retry(
        self,
        fn: Callable[[], ModelResponse],
        max_retries: int = 5,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
    ) -> ModelResponse:
        """Exponential backoff retry with jitter.

        Retry strategy (inspired by xAI Grok Build's sampler retry engine):
          - Layer 1: Network errors (ConnectError, TimeoutException, etc.) → retry with backoff
          - Layer 2: HTTP 429 (rate limit) → retry with longer backoff + respect Retry-After
          - Layer 3: HTTP 5xx (server errors) → retry with backoff
          - No retry: HTTP 4xx (except 429), programming errors (ValueError, TypeError)

        Uses jitter (+/- 25%) to avoid thundering herd on shared infrastructure.
        Caps backoff at max_delay to prevent unbounded wait.
        """
        _RETRYABLE_EXC = self.RETRYABLE_EXC
        _NON_RETRYABLE_EXC = self.NON_RETRYABLE_EXC

        last_error: ModelResponse | None = None
        for attempt in range(max_retries):
            try:
                resp = fn()
                if resp.raw and isinstance(resp.raw, dict):
                    status = resp.raw.get("status", 0)
                    if status == 429:
                        # Rate limit: use longer backoff.
                        last_error = resp
                        retry_after = float(resp.raw.get("retry_after", 0)) or base_delay * 4
                        delay = self._backoff_delay(attempt, base_delay=retry_after, max_delay=max_delay)
                        time.sleep(delay)
                        continue
                    if 500 <= status < 600:
                        # Server error: retryable with standard backoff.
                        last_error = resp
                        delay = self._backoff_delay(attempt, base_delay, max_delay)
                        time.sleep(delay)
                        continue
                return resp
            except _NON_RETRYABLE_EXC:
                raise
            except _RETRYABLE_EXC as e:
                last_error = ModelResponse(
                    content=f"[API error (attempt {attempt + 1}/{max_retries}): {e}]",
                    stop_reason=StopReason.STOP,
                )
                if attempt < max_retries - 1:
                    delay = self._backoff_delay(attempt, base_delay, max_delay)
                    time.sleep(delay)
                    continue
            except Exception as e:
                last_error = ModelResponse(
                    content=f"[API error (attempt {attempt + 1}/{max_retries}): {e}]",
                    stop_reason=StopReason.STOP,
                )
                if attempt < max_retries - 1:
                    delay = self._backoff_delay(attempt, base_delay, max_delay)
                    time.sleep(delay)
                    continue
        return last_error or ModelResponse(
            content=f"[API error after {max_retries} retries]",
            stop_reason=StopReason.STOP,
        )

    # ── Token estimation (rough) ──

    def estimate_tokens(self, messages: list[Any], text: str = "") -> int:
        """Rough token count estimation.

        Adapters can override this for precise counting (e.g., using tiktoken).
        Default: chars / 4 + message overhead.
        """
        total_chars = len(text)
        for m in messages:
            content = getattr(m, "content", "") or ""
            total_chars += len(content)
            tool_calls = getattr(m, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    total_chars += len(getattr(tc, "tool_id", "")) + len(
                        str(getattr(tc, "args", {}))
                    )
        overhead = len(messages) * 10
        return int(total_chars / 4.0) + overhead