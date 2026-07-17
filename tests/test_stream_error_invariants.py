"""AgentLoop stream error invariant test (A1: no silent truncation).

IPR-0: each test must contain a counterexample.

Protected invariants:
  1. A streaming exception in complete_stream propagates to step()'s
     terminal handler — it is NOT silently downgraded to a STOP response.
  2. loop._last_stream_error is set after the failure.
  3. A warning log message is emitted (observable via caplog).
"""

from __future__ import annotations

from typing import Any, Iterator

from zall.core.accountability import Evidence, Judge, JudgeVerdict
from zall.core.action import Action
from zall.core.context import Context
from zall.core.gate import UserResponder, UserResponse, UserResponseType
from zall.core.goal import (
    AcceptanceContract, GoalStatement, GoalTriple, GoalType, TerminationState,
)
from zall.core.loop import AgentLoop
from zall.core.loop_config import AgentConfig
from zall.core.model import (
    Message, ModelResponse, StopReason, ToolCall, ToolChoice,
)
from zall.core.safety import RuleSet, SafeLevel
from zall.core.tool import Tool, ToolRegistry, ToolResult


# ──────────────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────────────


class _CrashedStreamAdapter:
    """Streaming adapter that raises an exception mid-stream.

    This simulates a network/API failure during streaming — the exact
    scenario that was previously silently swallowed by _call_model_stream.
    """

    __test__ = False

    def __init__(self) -> None:
        self._called = False

    @property
    def model_name(self) -> str:
        return "crashed-stream"

    def complete(self, messages, tools, tool_choice=ToolChoice.AUTO) -> ModelResponse:
        return ModelResponse(content="blocking fallback", stop_reason=StopReason.STOP)

    def complete_stream(self, messages, tools, tool_choice=ToolChoice.AUTO) -> Iterator[tuple[str, ModelResponse]]:
        # Yield a few tokens first, then crash
        yield ("He", ModelResponse(content="He", stop_reason=StopReason.STOP))
        yield ("llo", ModelResponse(content="Hello", stop_reason=StopReason.STOP))
        raise RuntimeError("simulated stream failure (A1 test)")


class _FakeTool(Tool):
    def __init__(self) -> None:
        self._call_count = 0

    @property
    def tool_id(self) -> str:
        return "test_tool"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "name": "test_tool",
            "description": "test tool",
            "input_schema": {"type": "object", "properties": {}},
        }

    def execute(self, args: dict[str, Any], context: Context | None = None) -> ToolResult:
        self._call_count += 1
        return ToolResult(success=True, output="ok")


class _AllowAllResponder(UserResponder):
    """Responder that auto-approves everything."""
    def respond(self, kind: str, data: dict[str, Any]) -> UserResponse:
        return UserResponse(type=UserResponseType.APPROVE)


class _NoopJudge(Judge):
    def judge(self, action: Action, context: Context) -> Evidence:
        return Evidence(verdict=JudgeVerdict.NEUTRAL, reasoning="test")


# ──────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────


class _CwdMetaStub:
    __test__ = False
    def __init__(self) -> None:
        self.cwd_path = "/home/user/project"
        self.git_branch = "main"
        self.git_remote = "origin"


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


def _make_loop(*, adapter: Any | None = None) -> AgentLoop:
    """Minimal AgentLoop with streaming enabled."""
    if adapter is None:
        adapter = _CrashedStreamAdapter()
    tool = _FakeTool()
    registry = ToolRegistry(tools=[tool])
    config = AgentConfig(
        judge=_NoopJudge(),
        max_steps=5,
        stream=True,  # enable streaming so complete_stream is called
    )
    _ctx = Context(
        user_raw="test",
        cwd_meta=_CwdMetaStub(),
    )
    loop = AgentLoop(
        model=adapter,
        tools=registry,
        rules=RuleSet(safe_level=SafeLevel.WHITELIST),
        goal=_make_goal(),
        context=_ctx,
        user_responder=_AllowAllResponder(),
        config=config,
    )
    return loop


# ──────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────


def test_stream_error_propagates_to_terminal_egress() -> None:
    """A1: Streaming exception must produce a terminal error, not a silent STOP."""
    loop = _make_loop()
    loop.add_user_message("Hello, crash!")

    result = loop.step()

    # The step should be terminal with an error, NOT a silent STOP
    assert result.is_terminal, (
        f"expected terminal step result, got kind={result.kind}; "
        "stream exception was swallowed (A1 regression)"
    )
    assert result.egress is not None, "terminal step must have egress"
    assert result.egress.error is not None, "terminal egress must carry error message"
    assert "simulated stream failure" in result.egress.error, (
        f"expected 'simulated stream failure' in error, got: {result.egress.error}"
    )
    assert loop._last_stream_error is not None, "loop._last_stream_error must be set"
    assert isinstance(loop._last_stream_error, RuntimeError), (
        f"expected RuntimeError, got {type(loop._last_stream_error)}"
    )


def test_stream_error_logs_warning(caplog: Any) -> None:
    """A1: Streaming exception must emit a log warning."""
    import logging as _log
    caplog.set_level(_log.WARNING, logger="zall.core.loop")

    loop = _make_loop()
    loop.add_user_message("Hello, crash!")

    with caplog.at_level(_log.WARNING, logger="zall.core.loop"):
        loop.step()

    # The log should contain the stream failure message
    found = any(
        "stream model call failed" in rec.message
        for rec in caplog.records
    )
    assert found, "expected warning log about stream failure in zall.core.loop"


def test_stream_error_non_stream_adapter_unchanged() -> None:
    """A1: Non-streaming (blocking) path is unaffected by the fix."""
    class _BlockingAdapter:
        __test__ = False
        @property
        def model_name(self) -> str:
            return "blocking"
        def complete(self, messages, tools, tool_choice=ToolChoice.AUTO) -> ModelResponse:
            return ModelResponse(content="ok", stop_reason=StopReason.STOP)
        # no complete_stream — loop will use blocking path

    loop = _make_loop(adapter=_BlockingAdapter())
    loop.add_user_message("Hello")
    result = loop.step()
    assert not result.is_terminal, (
        f"blocking adapter should not be terminal, got kind={result.kind}"
    )


def test_stream_error_does_not_affect_healthy_stream() -> None:
    """A1: Healthy streaming still works after the fix."""
    class _HealthyStreamAdapter:
        __test__ = False
        @property
        def model_name(self) -> str:
            return "healthy"
        def complete(self, messages, tools, tool_choice=ToolChoice.AUTO) -> ModelResponse:
            return ModelResponse(content="done", stop_reason=StopReason.STOP)
        def complete_stream(self, messages, tools, tool_choice=ToolChoice.AUTO) -> Iterator[tuple[str, ModelResponse]]:
            yield ("h", ModelResponse(content="h", stop_reason=StopReason.STOP))
            yield ("i", ModelResponse(content="hi", stop_reason=StopReason.STOP))
            yield ("", ModelResponse(content="hi", stop_reason=StopReason.STOP))

    loop = _make_loop(adapter=_HealthyStreamAdapter())
    loop.add_user_message("Hello")
    result = loop.step()
    assert result.kind in ("awaiting_input", "tool_used"), (
        f"healthy stream should produce awaiting_input or tool_used, got kind={result.kind}"
    )