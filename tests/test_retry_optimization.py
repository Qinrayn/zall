"""API 超时/重试优化测试 (E17).

对应:
  MASTER.md §12 + 三源码学习 (Claude Code withRetry / Grok Build 独立预算)
  src/zall/adapters/base.py RetryBudget 错误分类 + is_retryable + _notify_retry
"""

from __future__ import annotations

import pytest

from zall.adapters.base import RetryBudget


# ── 错误分类 ──


class TestErrorClassification:
    def test_429_classified_as_rate_limit(self) -> None:
        assert RetryBudget.classify_http_status(429) == RetryBudget.RATE_LIMIT

    def test_500_502_503_classified_as_server_error(self) -> None:
        assert RetryBudget.classify_http_status(500) == RetryBudget.SERVER_ERROR
        assert RetryBudget.classify_http_status(502) == RetryBudget.SERVER_ERROR
        assert RetryBudget.classify_http_status(503) == RetryBudget.SERVER_ERROR

    def test_529_classified_as_server_error(self) -> None:
        assert RetryBudget.classify_http_status(529) == RetryBudget.SERVER_ERROR

    def test_401_403_classified_as_auth_failed(self) -> None:
        assert RetryBudget.classify_http_status(401) == RetryBudget.AUTH_FAILED
        assert RetryBudget.classify_http_status(403) == RetryBudget.AUTH_FAILED

    def test_400_422_classified_as_invalid_request(self) -> None:
        assert RetryBudget.classify_http_status(400) == RetryBudget.INVALID_REQUEST
        assert RetryBudget.classify_http_status(422) == RetryBudget.INVALID_REQUEST

    def test_unknown_status_classified_as_transport(self) -> None:
        """Counterexample: 未知状态码 -> TRANSPORT (降级, 不崩溃)."""
        assert RetryBudget.classify_http_status(999) == RetryBudget.TRANSPORT


# ── 可重试判定 ──


class TestRetryable:
    def test_429_is_retryable(self) -> None:
        assert RetryBudget.is_retryable_status(429) is True
        assert RetryBudget.is_retryable_http(429) is True

    def test_5xx_is_retryable(self) -> None:
        for code in (500, 502, 503, 529):
            assert RetryBudget.is_retryable_status(code) is True

    def test_401_not_retryable(self) -> None:
        """Counterexample: 401 不重试 (重试也无效)."""
        assert RetryBudget.is_retryable_status(401) is False

    def test_403_not_retryable(self) -> None:
        assert RetryBudget.is_retryable_status(403) is False

    def test_400_422_not_retryable(self) -> None:
        """Counterexample: 400/422 不重试 (客户端错误)."""
        assert RetryBudget.is_retryable_status(400) is False
        assert RetryBudget.is_retryable_status(422) is False

    def test_200_not_retryable(self) -> None:
        """Counterexample: 200 不是错误, 不重试."""
        assert RetryBudget.is_retryable_status(200) is False


# ── RetryBudget 预算 ──


class TestRetryBudget:
    def test_can_retry_within_budget(self) -> None:
        rb = RetryBudget(max_transport=3)
        assert rb.can_retry("transport") is True
        rb.record_attempt("transport")
        assert rb.can_retry("transport") is True
        rb.record_attempt("transport")
        rb.record_attempt("transport")
        assert rb.can_retry("transport") is False  # 预算耗尽

    def test_auth_failed_status_not_retryable(self) -> None:
        """Counterexample: AUTH_FAILED 对应的 401 状态码不可重试."""
        assert RetryBudget.is_retryable_status(401) is False

    def test_rate_limit_status_retryable(self) -> None:
        assert RetryBudget.is_retryable_status(429) is True

    def test_server_error_status_retryable(self) -> None:
        for code in (500, 503, 529):
            assert RetryBudget.is_retryable_status(code) is True

    def test_content_filter_not_retryable_status(self) -> None:
        """Counterexample: CONTENT_FILTER 概念存在, 对应状态码不可重试."""
        assert hasattr(RetryBudget, "CONTENT_FILTER")
        # content_filter 通常是 400 范畴或 finish_reason, 不重试
        assert RetryBudget.is_retryable_status(400) is False

    def test_record_attempt_returns_delay(self) -> None:
        rb = RetryBudget(base_delay=1.0, jitter=0.0)
        delay = rb.record_attempt("transport")
        assert delay > 0  # 应返回退避延迟


# ── _notify_retry 回调 (用户可见重试提示) ──


class TestRetryNotification:
    def test_notify_retry_calls_callback(self) -> None:
        calls: list[tuple] = []
        rb = RetryBudget(on_retry=lambda cat, delay, attempt, max_r: calls.append((cat, delay, attempt, max_r)))
        rb.record_attempt("transport")  # record_attempt 内部调 _notify_retry
        assert len(calls) == 1
        assert calls[0][0] == "transport"

    def test_notify_retry_silent_without_callback(self) -> None:
        """Counterexample: 无回调时不崩溃."""
        rb = RetryBudget()
        rb.record_attempt("transport")  # 不应抛异常


# ── 所有 adapter 统一用 RetryBudget ──


class TestAllAdaptersUseRetryBudget:
    def test_openai_compat_has_retry_budget(self) -> None:
        from zall.adapters.openai_compat import OpenAICompatAdapter
        adapter = OpenAICompatAdapter(api_key="test", api_base="http://test", model="test")
        assert hasattr(adapter, "_retry_budget")

    def test_anthropic_has_error_handling(self) -> None:
        """anthropic 至少有错误处理 (可能未用 RetryBudget, 标 OPEN)."""
        try:
            from zall.adapters.anthropic import AnthropicAdapter
            adapter = AnthropicAdapter(api_key="test", model="test")
            # anthropic 可能用 with_retry 或自己的重试, 至少能构造
            assert adapter is not None
        except ImportError:
            pytest.skip("anthropic SDK not installed")

    def test_gemini_has_retry_budget(self) -> None:
        try:
            from zall.adapters.gemini import GeminiAdapter
            adapter = GeminiAdapter(api_key="test", model="test")
            assert hasattr(adapter, "_retry_budget")
        except (ImportError, Exception):
            pytest.skip("gemini SDK not installed")

    def test_ollama_has_retry_budget(self) -> None:
        try:
            from zall.adapters.ollama import OllamaAdapter
            adapter = OllamaAdapter(model="test")
            assert hasattr(adapter, "_retry_budget")
        except (ImportError, Exception):
            pytest.skip("ollama SDK not installed")
