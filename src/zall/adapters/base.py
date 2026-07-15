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
import time
from typing import Any, cast, ClassVar

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

    def make_error_response(self, status_code: int, body: str) -> ModelResponse:
        """Build a user-friendly error ModelResponse.

        Maps common HTTP error codes to readable hints, avoiding raw JSON exposure.
        """
        hint = _ERROR_MAP.get(
            status_code,
            f"API error (HTTP {status_code}). Check your config with /doctor.",
        )
        return ModelResponse(
            content=f"[{hint}]",
            stop_reason=StopReason.STOP,
            raw={"status": status_code, "body": body[:500]},
        )

    # ── Retry ──

    def with_retry(
        self,
        fn: Any,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> ModelResponse:
        """Exponential backoff retry.

        Only retries on recoverable errors (429 rate limit, 5xx server errors,
        network exceptions). Does NOT retry on client errors (400/401/403/404)
        or programming errors (ValueError/TypeError).
        """
        _RETRYABLE_EXC = self.RETRYABLE_EXC
        _NON_RETRYABLE_EXC = self.NON_RETRYABLE_EXC

        last_error: ModelResponse | None = None
        for attempt in range(max_retries):
            try:
                resp = fn()
                if resp.raw and isinstance(resp.raw, dict):
                    status = resp.raw.get("status", 0)
                    if status in (429,) or (500 <= status < 600):
                        # Retryable HTTP error.
                        last_error = resp
                        delay = base_delay * (2 ** attempt)
                        time.sleep(delay)
                        continue
                return cast(ModelResponse, resp)
            except _NON_RETRYABLE_EXC:
                # Programming error: do not retry, re-raise.
                raise
            except _RETRYABLE_EXC as e:
                # Network error: retryable.
                last_error = ModelResponse(
                    content=f"[API error (attempt {attempt + 1}/{max_retries}): {e}]",
                    stop_reason=StopReason.STOP,
                )
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
            except Exception as e:
                # Other exceptions: try once more, do not retry indefinitely.
                last_error = ModelResponse(
                    content=f"[API error (attempt {attempt + 1}/{max_retries}): {e}]",
                    stop_reason=StopReason.STOP,
                )
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
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
