"""Retry visibility + Retry-After budget invariants (2026-07-26 bugfix, G3).

IPR-0: each test must contain a counterexample.

Protected invariants:
  1. I-RETRY-BUDGET: a persistent 429 WITH a valid Retry-After header must
     still consume the api retry budget and terminate. (Counterexample: before
     the fix, `delay = float(retry_after)` skipped record_attempt — the loop
     retried forever; the stub raises after a hard cap to make that failure
     mode a test failure, not a hang.)
  2. I-RETRY-VISIBLE: RetryBudget notifies its on_retry callback on every
     consumed attempt, and BaseAdapter.set_retry_callback wires it through.
  3. I-402: HTTP 402 has a user-facing hint in _ERROR_MAP, classifies as
     INVALID_REQUEST and is not retryable.
  4. I-RETRY-EVENT: AgentLoop wires adapter.set_retry_callback (duck-typed)
     and re-emits notifications as kind="retry" LoopEvents.
"""

from __future__ import annotations

from typing import Any

import pytest

from zall.adapters.base import _ERROR_MAP, RETRY_REASON, RetryBudget
from zall.adapters.openai_compat import OpenAICompatAdapter
from zall.core.accountability import Evidence, Judge, JudgeVerdict
from zall.core.action import Action
from zall.core.context import Context
from zall.core.gate import UserResponder, UserResponse, UserResponseType
from zall.core.goal import (
    AcceptanceContract, GoalStatement, GoalTriple, GoalType, TerminationState,
)
from zall.core.loop import AgentLoop
from zall.core.loop_config import AgentConfig
from zall.core.model import ModelResponse, StopReason, ToolChoice
from zall.core.safety import RuleSet, SafeLevel
from zall.core.tool import ToolRegistry


# ──────────────────────────────────────────────────────────────────────────
# Stubs
# ──────────────────────────────────────────────────────────────────────────


class _Always429Client:
    """httpx.Client stand-in: every POST returns 429 with a Retry-After header."""

    __test__ = False
    HARD_CAP = 20  # counterexample guard: infinite retry becomes a failure, not a hang

    def __init__(self, retry_after: str = "0.001") -> None:
        self.post_count = 0
        self._retry_after = retry_after

    def post(self, url: str, **kwargs: Any) -> Any:
        self.post_count += 1
        if self.post_count > self.HARD_CAP:
            raise AssertionError(
                "I-RETRY-BUDGET violated: persistent 429 + Retry-After retried "
                f"more than {self.HARD_CAP} times (budget not consumed)"
            )
        client = self

        class _Resp:
            status_code = 429
            headers = {"retry-after": client._retry_after}
            text = '{"error": "rate limited"}'

        return _Resp()

    def close(self) -> None:
        pass


def _make_adapter(client: Any) -> OpenAICompatAdapter:
    adapter = OpenAICompatAdapter(api_key="test-key", api_base="http://localhost:1", model="m")
    adapter._client.close()
    adapter._client = client
    # keep tests fast: no jitter surprises beyond header-provided delays
    adapter._retry_budget.base_delay = 0.001
    return adapter


# ──────────────────────────────────────────────────────────────────────────
# 1. I-RETRY-BUDGET
# ──────────────────────────────────────────────────────────────────────────


def test_retry_after_consumes_budget() -> None:
    """Persistent 429 with valid Retry-After terminates within the api budget."""
    client = _Always429Client(retry_after="0.001")
    adapter = _make_adapter(client)
    resp = adapter.complete([], [], ToolChoice.AUTO)
    # max_api=5 retries + 1 final non-retried attempt
    assert client.post_count <= adapter._retry_budget.max_api + 1
    assert resp.stop_reason == StopReason.STOP
    assert "rate limit" in resp.content.lower()  # _ERROR_MAP[429] surfaced


def test_retry_after_invalid_header_still_consumes_budget() -> None:
    """Unparseable Retry-After falls back to exponential backoff, still bounded."""
    client = _Always429Client(retry_after="not-a-number")
    adapter = _make_adapter(client)
    adapter.complete([], [], ToolChoice.AUTO)
    assert client.post_count <= adapter._retry_budget.max_api + 1


# ──────────────────────────────────────────────────────────────────────────
# 2. I-RETRY-VISIBLE
# ──────────────────────────────────────────────────────────────────────────


def test_retry_callback_fires_per_attempt() -> None:
    """set_retry_callback receives (category, delay, attempt, max) per retry."""
    client = _Always429Client(retry_after="0.001")
    adapter = _make_adapter(client)
    calls: list[tuple[str, float, int, int]] = []
    adapter.set_retry_callback(lambda c, d, a, m: calls.append((c, d, a, m)))
    adapter.complete([], [], ToolChoice.AUTO)
    assert len(calls) == adapter._retry_budget.max_api
    assert all(c[0] == "api" for c in calls)
    assert [c[2] for c in calls] == list(range(1, len(calls) + 1))  # attempts 1..N
    assert all(c[3] == adapter._retry_budget.max_api for c in calls)


def test_retry_callback_exception_swallowed() -> None:
    """Counterexample: a crashing callback must not break the retry path."""
    client = _Always429Client(retry_after="0.001")
    adapter = _make_adapter(client)
    adapter.set_retry_callback(lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    resp = adapter.complete([], [], ToolChoice.AUTO)  # must not raise
    assert resp.stop_reason == StopReason.STOP


def test_budget_without_callback_is_silent() -> None:
    """No callback set (default) → retry path unchanged, no error."""
    budget = RetryBudget(base_delay=0.001)
    delay = budget.record_attempt("api")
    assert delay >= 0.0
    assert budget.get_summary()["api"] == 1


# ──────────────────────────────────────────────────────────────────────────
# 3. I-402 + reason table
# ──────────────────────────────────────────────────────────────────────────


def test_402_has_user_hint_and_is_not_retryable() -> None:
    assert 402 in _ERROR_MAP
    assert "top up" in _ERROR_MAP[402].lower() or "/model" in _ERROR_MAP[402]
    assert RetryBudget.classify_http_status(402) == RetryBudget.INVALID_REQUEST
    assert RetryBudget.is_retryable_status(402) is False


def test_retry_reason_covers_all_budget_categories() -> None:
    """Every budget category has a human label (single truth source)."""
    budget = RetryBudget()
    for category in budget.get_summary():
        assert category in RETRY_REASON, f"RETRY_REASON missing '{category}'"


# ──────────────────────────────────────────────────────────────────────────
# 4. I-RETRY-EVENT (loop wiring)
# ──────────────────────────────────────────────────────────────────────────


class _CwdMetaStub:
    __test__ = False

    def __init__(self) -> None:
        self.cwd_path = "/home/user/project"
        self.git_branch = "main"
        self.git_remote = "origin"


class _AllowAllResponder(UserResponder):
    def respond(self, kind: str, data: dict[str, Any]) -> UserResponse:
        return UserResponse(type=UserResponseType.APPROVE)


class _NoopJudge(Judge):
    def judge(self, action: Action, context: Context) -> Evidence:
        return Evidence(verdict=JudgeVerdict.NEUTRAL, reasoning="test")


def _make_goal() -> GoalTriple:
    class _T:
        exposed_dependency_set = None

        def __call__(self, state: object) -> TerminationState:
            return TerminationState.UNDECIDABLE

    return GoalTriple(
        statement=GoalStatement(
            intent="x", rewriting="x", rewrite_confidence=1.0,
            goal_type=GoalType.DOCS, translation_of=("s",), added_intent=(),
        ),
        termination=_T(),
        acceptance=AcceptanceContract(baseline_frozen_at="abc"),
    )


class _RetryCapableAdapter:
    """Adapter stub exposing set_retry_callback (duck-typed contract)."""

    __test__ = False

    def __init__(self) -> None:
        self.retry_cb: Any = None

    @property
    def model_name(self) -> str:
        return "retry-capable"

    def set_retry_callback(self, cb: Any) -> None:
        self.retry_cb = cb

    def complete(self, messages, tools, tool_choice=ToolChoice.AUTO) -> ModelResponse:
        return ModelResponse(content="ok", stop_reason=StopReason.STOP)


class _PlainAdapter:
    """Counterexample adapter WITHOUT set_retry_callback — loop must not crash."""

    __test__ = False

    @property
    def model_name(self) -> str:
        return "plain"

    def complete(self, messages, tools, tool_choice=ToolChoice.AUTO) -> ModelResponse:
        return ModelResponse(content="ok", stop_reason=StopReason.STOP)


def _make_loop(adapter: Any, observer: Any = None) -> AgentLoop:
    config = AgentConfig(judge=_NoopJudge(), max_steps=10, stream=False,
                         observer=observer)
    return AgentLoop(
        model=adapter,
        tools=ToolRegistry(tools=[]),
        rules=RuleSet(safe_level=SafeLevel.WHITELIST),
        goal=_make_goal(),
        context=Context(user_raw="test", cwd_meta=_CwdMetaStub()),
        user_responder=_AllowAllResponder(),
        config=config,
    )


def test_loop_wires_retry_callback_and_emits_retry_event() -> None:
    """Loop injects a callback that re-emits kind='retry' LoopEvents."""
    events: list[Any] = []
    adapter = _RetryCapableAdapter()
    _make_loop(adapter, observer=events.append)
    assert adapter.retry_cb is not None, "loop did not wire set_retry_callback"
    adapter.retry_cb("api", 2.5, 2, 5)
    retry_events = [e for e in events if e.kind == "retry"]
    assert len(retry_events) == 1
    payload = retry_events[0].payload
    # legacy observer 适配层会额外注入 step — 断言子集即可
    expected = {"category": "api", "delay": 2.5, "attempt": 2, "max_attempts": 5}
    assert expected.items() <= payload.items()


def test_loop_tolerates_adapter_without_retry_callback() -> None:
    """Counterexample: plain adapter (no set_retry_callback) must still build."""
    loop = _make_loop(_PlainAdapter())
    assert loop is not None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
