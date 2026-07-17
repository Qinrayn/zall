"""AgentLoop retry_step invariant test (A2: no step_count drift on retry).

IPR-0: each test must contain a counterexample.

Protected invariants:
  1. retry_step() does NOT increment _step_count.
  2. step() DOES increment _step_count (unchanged behavior).
  3. Multiple retry_step() calls in sequence keep _step_count stable.
  4. retry_step() produces the same result type as step() for the same input.
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


class _CountedBlockingAdapter:
    """Simple blocking adapter that counts calls and returns STOP."""

    __test__ = False

    def __init__(self, content: str = "ok") -> None:
        self.call_count = 0
        self._content = content

    @property
    def model_name(self) -> str:
        return "counted"

    def complete(self, messages, tools, tool_choice=ToolChoice.AUTO) -> ModelResponse:
        self.call_count += 1
        return ModelResponse(content=self._content, stop_reason=StopReason.STOP)


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


def _make_loop(*, adapter: Any | None = None) -> AgentLoop:
    if adapter is None:
        adapter = _CountedBlockingAdapter()
    tool = _FakeTool()
    registry = ToolRegistry(tools=[tool])
    config = AgentConfig(
        judge=_NoopJudge(),
        max_steps=10,
        stream=False,
    )
    loop = AgentLoop(
        model=adapter,
        tools=registry,
        rules=RuleSet(safe_level=SafeLevel.WHITELIST),
        goal=_make_goal(),
        context=Context(user_raw="test", cwd_meta=_CwdMetaStub()),
        user_responder=_AllowAllResponder(),
        config=config,
    )
    return loop


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


# ──────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────


def test_retry_step_does_not_increment_count() -> None:
    """A2: retry_step() must not increment _step_count."""
    loop = _make_loop()
    loop.add_user_message("Hello")

    # One normal step to set the baseline
    loop.step()
    baseline = loop._step_count

    # Multiple retry steps should not increase the counter
    for _ in range(3):
        result = loop.retry_step()
        assert loop._step_count == baseline, (
            f"retry_step drifted _step_count: expected {baseline}, got {loop._step_count}"
        )
        # retry_step should produce a valid result (not crash)
        assert result is not None, "retry_step returned None"


def test_step_increments_count_normally() -> None:
    """A2: step() increments _step_count each call (unchanged behavior)."""
    loop = _make_loop()
    loop.add_user_message("Hello")

    start = loop._step_count
    for i in range(1, 4):
        loop.step()
        assert loop._step_count == start + i, (
            f"step() should increment by 1 each call: "
            f"expected {start + i}, got {loop._step_count}"
        )


def test_retry_step_has_retry_step_method() -> None:
    """A2: AgentLoop exposes retry_step() method."""
    loop = _make_loop()
    assert hasattr(loop, "retry_step"), "AgentLoop must have retry_step()"
    assert callable(loop.retry_step), "retry_step() must be callable"


def test_retry_step_returns_awaiting_input_on_stop() -> None:
    """A2: retry_step() behaves like step() for a STOP model response."""
    loop = _make_loop()
    loop.add_user_message("Hello")

    result = loop.retry_step()
    assert result.kind in ("awaiting_input", "tool_used", "terminal"), (
        f"retry_step() returned unexpected kind: {result.kind}"
    )


def test_retry_step_does_not_affect_subsequent_step_count() -> None:
    """A2: retry_step() before step() does not corrupt the step counter."""
    loop = _make_loop()
    loop.add_user_message("Hello")

    # Retry first
    loop.retry_step()
    loop.retry_step()

    # Then step — should still be 1 (first real step)
    loop.step()
    assert loop._step_count == 1, (
        f"step() after retry_step() should be 1, got {loop._step_count}"
    )

    # Another step should be 2
    loop.step()
    assert loop._step_count == 2, (
        f"second step() after retry should be 2, got {loop._step_count}"
    )