"""timeline 完整性 invariant test (§6.2 replay 前提).

IPR-0: each test must contain a counterexample.

Protected core invariants (§6.2 replay 前提):
  1. model_call 事件 payload 含完整 content (不只 content_length)
  2. model_call 事件 payload 含完整 tool_calls (不只 tool_calls_count)
  3. tool_call_end 事件 payload 含完整 output (不只 output_length)
  4. 这些完整数据足以重建 ModelResponse (replay 用)
"""

from __future__ import annotations

from zall.core.accountability import Evidence, Judge, JudgeVerdict
from zall.core.action import Action
from zall.core.context import Context
from zall.core.gate import UserResponder, UserResponse, UserResponseType
from zall.core.goal import (
    AcceptanceContract, GoalStatement, GoalTriple, GoalType, TerminationState,
)
from zall.core.loop import AgentLoop
from zall.core.loop_config import AgentConfig
from zall.core.model import Message, ModelResponse, StopReason, ToolCall, ToolChoice
from zall.core.safety import RuleSet, SafeLevel
from zall.core.tool import Tool, ToolRegistry, ToolResult
from zall.core.verifiability import EventType


# ──────────────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────────────


class _ScriptedAdapter:
    __test__ = False

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._idx = 0

    @property
    def model_name(self) -> str:
        return "fake-timeline"

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


def _make_loop(adapter) -> AgentLoop:
    return AgentLoop(
        model=adapter,
        tools=ToolRegistry(tools=(_EchoTool(),)),
        rules=RuleSet(),
        goal=_make_goal(),
        context=Context(user_raw="x", cwd_meta=_CwdMetaStub()),
        user_responder=_AutoAcceptResponder(),
        config=AgentConfig(judge=_MetJudge()),
    )


# ──────────────────────────────────────────────────────────────────────────
# model_call payload 完整性
# ──────────────────────────────────────────────────────────────────────────


class TestModelCallPayloadComplete:
    def test_model_call_has_full_content(self) -> None:
        """Happy path: model_call payload 含完整 content (不只 content_length).

        §6.2 replay 需要完整 content 重建 ModelResponse.
        """
        adapter = _ScriptedAdapter([
            ModelResponse(
                content="let me echo hello world",
                tool_calls=(ToolCall(id="tc1", tool_id="echo", args={"text": "hi"}),),
                stop_reason=StopReason.TOOL_USE,
            ),
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)
        loop.run()

        # 找第一个 model_call event
        model_calls = [e for e in loop.recorder.events if e.event_type == EventType.MODEL_CALL]
        assert len(model_calls) >= 1
        payload = model_calls[0].payload
        # 完整 content (不只 content_length)
        assert "content" in payload
        assert payload["content"] == "let me echo hello world"

    def test_model_call_has_full_tool_calls(self) -> None:
        """Happy path: model_call payload 含完整 tool_calls (不只 count).

        §6.2 replay 需要完整 tool_calls 重建 ModelResponse.
        """
        adapter = _ScriptedAdapter([
            ModelResponse(
                content="echoing",
                tool_calls=(
                    ToolCall(id="tc1", tool_id="echo", args={"text": "hi"}),
                    ToolCall(id="tc2", tool_id="echo", args={"text": "bye"}),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)
        loop.run()

        model_calls = [e for e in loop.recorder.events if e.event_type == EventType.MODEL_CALL]
        payload = model_calls[0].payload
        assert "tool_calls" in payload
        tcs = payload["tool_calls"]
        assert len(tcs) == 2
        assert tcs[0]["tool_id"] == "echo"
        assert tcs[0]["args"] == {"text": "hi"}
        assert tcs[1]["args"] == {"text": "bye"}

    def test_model_call_summary_fields_preserved(self) -> None:
        """Counterexample: 加完整数据后, 原有digest字段 (content_length/tool_calls_count) 仍在.

        如果加完整数据时删了摘要字段, 依赖摘要的代码会崩.
        """
        adapter = _ScriptedAdapter([
            ModelResponse(content="hi", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)
        loop.run()

        model_calls = [e for e in loop.recorder.events if e.event_type == EventType.MODEL_CALL]
        payload = model_calls[0].payload
        # 原有digest字段仍在
        assert "content_length" in payload
        assert "tool_calls_count" in payload
        assert "stop_reason" in payload
        assert "model" in payload


# ──────────────────────────────────────────────────────────────────────────
# tool_call_end payload 完整性
# ──────────────────────────────────────────────────────────────────────────


class TestToolCallEndPayloadComplete:
    def test_tool_call_end_has_full_output(self) -> None:
        """Happy path: tool_call_end payload 含完整 output (不只 output_length).

        §6.2 replay 需要完整 output 重建 ToolResult.
        """
        adapter = _ScriptedAdapter([
            ModelResponse(
                content="echo",
                tool_calls=(ToolCall(id="tc1", tool_id="echo", args={"text": "hello"}),),
                stop_reason=StopReason.TOOL_USE,
            ),
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)
        loop.run()

        tool_ends = [e for e in loop.recorder.events if e.event_type == EventType.TOOL_CALL_END]
        assert len(tool_ends) == 1
        payload = tool_ends[0].payload
        assert "output" in payload
        assert payload["output"] == "echoed: hello"

    def test_tool_call_end_summary_preserved(self) -> None:
        """Counterexample: 加完整 output 后, 原有 output_length 仍在."""
        adapter = _ScriptedAdapter([
            ModelResponse(
                content="echo",
                tool_calls=(ToolCall(id="tc1", tool_id="echo", args={"text": "x"}),),
                stop_reason=StopReason.TOOL_USE,
            ),
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)
        loop.run()

        tool_ends = [e for e in loop.recorder.events if e.event_type == EventType.TOOL_CALL_END]
        payload = tool_ends[0].payload
        assert "output_length" in payload
        assert "success" in payload
        assert "tool_id" in payload
