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
from typing import Any, Callable, cast, ClassVar

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
    403: (
        "API access denied. Your API key may not have permission "
        "for this model or endpoint. Try /model to switch models."
    ),
    404: (
        "API endpoint not found. Check your api_base setting."
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
        # Warn on non-HTTPS API base URLs.
        if self._api_base and not self._api_base.startswith("https://"):
            import sys
            print(f"  ⚠ WARNING: API base URL is not HTTPS: {self._api_base}",
                  file=sys.stderr)
        # Subclasses should create their own HTTP client.

    def close(self) -> None:
        """Close the HTTP client. Subclasses should override."""
        pass

    @property
    def model_name(self) -> str:
        return self._model

    # ── Error handling ──

    def make_error_response(self, status_code: int, body: str, raw: dict[str, Any] | None = None) -> ModelResponse:
        """Build a user-friendly error ModelResponse.

        Maps common HTTP error codes to readable hints, avoiding raw JSON exposure.
        """
        hint = _ERROR_MAP.get(
            status_code,
            f"API error (HTTP {status_code}). Check your config with /doctor.",
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
                return cast(ModelResponse, resp)
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
