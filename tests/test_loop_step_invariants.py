"""AgentLoop.step() invariant test (P3: chat mode seam).

IPR-0: each test must contain a counterexample.

Protected core invariants:
  1. run() 行for不变: step() 重构后, run() 产出的 RunEgress 与重构前等价
  2. step() STOP → awaiting_input (不自动终止, 对话模式用)
  3. step() TOOL_USE → tool_used (继续, non-终止)
  4. step() 异常 → terminal + egress
  5. 对话模式: add_user_message + step() 多轮, messages 持续增长
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
from zall.core.loop import AgentLoop, LoopEvent, RunEgress, StepResult
from zall.core.model import (
    Message, ModelResponse, StopReason, ToolCall, ToolChoice,
)
from zall.core.safety import RuleSet, SafeLevel
from zall.core.tool import Tool, ToolRegistry, ToolResult


# ──────────────────────────────────────────────────────────────────────────
# Fakes (复用 test_loop_stream 的范式)
# ──────────────────────────────────────────────────────────────────────────


class _ScriptedAdapter:
    __test__ = False

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._idx = 0

    @property
    def model_name(self) -> str:
        return "fake-step"

    def complete(self, messages, tools, tool_choice=ToolChoice.AUTO) -> ModelResponse:
        if self._idx >= len(self._responses):
            return ModelResponse(content="exhausted", stop_reason=StopReason.STOP)
        r = self._responses[self._idx]
        self._idx += 1
        return r


class _EchoTool:
    __test__ = False

    @property
    def tool_id(self) -> str:
        return "echo"

    @property
    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": "echo", "description": "echo",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
        }}

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output=f"echoed: {args.get('text', '')}")


class _AutoAcceptResponder:
    __test__ = False

    def ask(self, action: Action, judgement) -> UserResponse:
        if judgement.level == SafeLevel.BLACKLIST:
            return UserResponse(response_type=UserResponseType.REJECT)
        return UserResponse(response_type=UserResponseType.ACCEPT)


class _MetJudge:
    __test__ = False

    @property
    def judge_type(self) -> str:
        return "system"

    def __call__(self, evidence: Evidence) -> JudgeVerdict:
        return JudgeVerdict(state=TerminationState.MET, report="ok")


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


def _make_loop(adapter, judge=None) -> AgentLoop:
    return AgentLoop(
        model=adapter,
        tools=ToolRegistry(tools=(_EchoTool(),)),
        rules=RuleSet(),
        goal=_make_goal(),
        context=Context(user_raw="x", cwd_meta=_CwdMetaStub()),
        user_responder=_AutoAcceptResponder(),
        judge=judge,
    )


# ──────────────────────────────────────────────────────────────────────────
# run() 行for不变 (重构守护)
# ──────────────────────────────────────────────────────────────────────────


class TestRunUnchanged:
    def test_run_tool_then_stop(self) -> None:
        """Happy path: run() 重构后, tool→stop 仍产出correctly RunEgress."""
        adapter = _ScriptedAdapter([
            ModelResponse(
                content="echoing",
                tool_calls=(ToolCall(id="tc1", tool_id="echo", args={"text": "hi"}),),
                stop_reason=StopReason.TOOL_USE,
            ),
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, judge=_MetJudge())
        eg = loop.run()
        assert eg.final_state == TerminationState.MET
        assert eg.total_model_calls == 2
        assert eg.total_tool_calls == 1
        assert eg.error is None

    def test_run_immediate_stop(self) -> None:
        """Happy path: run() 重构后, immediate stop 仍correctly."""
        adapter = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, judge=None)
        eg = loop.run()
        assert eg.final_state == TerminationState.UNDECIDABLE
        assert eg.total_tool_calls == 0

    def test_run_hallucination_caught(self) -> None:
        """Counterexample: run() 重构后, TOOL_USE 无 tool_calls 仍被捕获 (PR-0)."""
        adapter = _ScriptedAdapter([
            ModelResponse(
                content="I ran grep",
                tool_calls=(),
                stop_reason=StopReason.TOOL_USE,
            ),
        ])
        loop = _make_loop(adapter, judge=None)
        eg = loop.run()
        assert eg.error is not None
        assert "hallucinated" in eg.error.lower() or "empty" in eg.error.lower()


# ──────────────────────────────────────────────────────────────────────────
# step() 单步语义
# ──────────────────────────────────────────────────────────────────────────


class TestStepSemantics:
    def test_step_tool_use_returns_tool_used(self) -> None:
        """Happy path: step() 遇 TOOL_USE → tool_used (non- terminal)."""
        adapter = _ScriptedAdapter([
            ModelResponse(
                content="echoing",
                tool_calls=(ToolCall(id="tc1", tool_id="echo", args={"text": "hi"}),),
                stop_reason=StopReason.TOOL_USE,
            ),
        ])
        loop = _make_loop(adapter)
        # run() 会init化 messages; step() directly调需要先init化
        loop._messages = [Message.user("x")]
        result = loop.step()
        assert result.kind == "tool_used"
        assert not result.is_terminal
        assert "echo" in result.tools_used

    def test_step_stop_returns_awaiting_input(self) -> None:
        """Happy path: step() 遇 STOP → awaiting_input (不自动terminate).

        对话模式: STOP 是暂停点, not终止.
        """
        adapter = _ScriptedAdapter([
            ModelResponse(content="hello there", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)
        loop._messages = [Message.user("x")]
        result = loop.step()
        assert result.kind == "awaiting_input"
        assert not result.is_terminal
        assert result.content == "hello there"
        assert result.egress is None  # non- terminal → 无 egress

    def test_step_terminal_has_egress(self) -> None:
        """Counterexample: terminal 时 egress mustnon-空.

        如果 terminal 但 egress=None, run() 会 assert fail.
        """
        # 用一个会触发exception的 adapter (complete 抛错)
        class _BoomAdapter:
            __test__ = False
            model_name = "boom"
            def complete(self, messages, tools, tool_choice=ToolChoice.AUTO) -> ModelResponse:
                raise RuntimeError("boom")
        loop = _make_loop(_BoomAdapter())  # type: ignore[arg-type]
        loop._messages = [Message.user("x")]
        result = loop.step()
        assert result.is_terminal
        assert result.egress is not None
        assert result.egress.error is not None


# ──────────────────────────────────────────────────────────────────────────
# 对话pattern: 多轮 step + add_user_message
# ──────────────────────────────────────────────────────────────────────────


class TestChatMode:
    def test_chat_multi_turn_messages_grow(self) -> None:
        """Happy path: 对话pattern多轮, messages 持续增长 (用户显式回灌).

        轮1: user "hi" → model "hello"
        轮2: user "bye" → model "see you"
        """
        adapter = _ScriptedAdapter([
            ModelResponse(content="hello", stop_reason=StopReason.STOP),
            ModelResponse(content="see you", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)
        # init化 (mock对话pattern启动)
        loop._messages = [Message.user("hi")]

        # 轮 1
        r1 = loop.step()
        assert r1.kind == "awaiting_input"
        assert r1.content == "hello"
        # messages: [user "hi", assistant "hello"]
        assert len(loop._messages) == 2

        # 用户回灌第二轮
        loop.add_user_message("bye")
        # messages: [user "hi", assistant "hello", user "bye"]
        assert len(loop._messages) == 3

        # 轮 2
        r2 = loop.step()
        assert r2.kind == "awaiting_input"
        assert r2.content == "see you"
        # messages: [..., user "bye", assistant "see you"]
        assert len(loop._messages) == 4

    def test_chat_finalize_undecidable(self) -> None:
        """Happy path: 对话结束 finalize → undecidable (不judgment met/not_met).

        对话没有"完成"概念 → 诚实退让 undecidable.
        """
        adapter = _ScriptedAdapter([
            ModelResponse(content="hello", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)
        loop._messages = [Message.user("hi")]
        loop.step()
        eg = loop.finalize()
        assert eg.final_state == TerminationState.UNDECIDABLE

    def test_chat_with_tool_use(self) -> None:
        """Happy path: 对话pattern也能调tool (security层照走)."""
        adapter = _ScriptedAdapter([
            ModelResponse(
                content="let me check",
                tool_calls=(ToolCall(id="tc1", tool_id="echo", args={"text": "x"}),),
                stop_reason=StopReason.TOOL_USE,
            ),
            ModelResponse(content="checked, all good", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)
        loop._messages = [Message.user("check x")]
        r1 = loop.step()
        assert r1.kind == "tool_used"
        assert "echo" in r1.tools_used
        # toolexecute了 (recorder 有记录)
        assert loop.recorder.verify_chain()
        # 继续到 STOP
        r2 = loop.step()
        assert r2.kind == "awaiting_input"
        assert r2.content == "checked, all good"

    def test_chat_does_not_call_judge(self) -> None:
        """Counterexample: 对话pattern不调 judge (对话无"完成"概念).

        finalize() directlyreturns undecidable, 不construct Evidence, 不调 judge.
        若调了 judge, 一个 met judge 会让对话"完成" → 错误.
        """
        class _TrackingJudge:
            __test__ = False
            judge_type = "system"
            def __init__(self):
                self.called = False
            def __call__(self, evidence: Evidence) -> JudgeVerdict:
                self.called = True
                return JudgeVerdict(state=TerminationState.MET)
        judge = _TrackingJudge()
        adapter = _ScriptedAdapter([
            ModelResponse(content="hi", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, judge=judge)  # type: ignore[arg-type]
        loop._messages = [Message.user("hi")]
        loop.step()
        loop.finalize()
        # judge 没被调
        assert not judge.called
