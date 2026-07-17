"""AgentLoop stream split invariant test (P2: streaming ≡ blocking).

IPR-0: each test must contain a counterexample.

Protected core invariants:
  1. stream=True 和 stream=False 产出等价的 RunEgress (final_state/step/tool/model_calls)
  2. stream=True 时 observer 收到 model_token 事件
  3. stream=False 时 observer 不收到 model_token 事件
  4. stream=True 但 adapter 不支持 complete_stream → 降级到blocking (does not crash)
  5. RunRecorder 不记 model_token (token 是呈现层, not审计轨迹)
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
from zall.core.loop_events import LoopEvent
from zall.core.model import (
    Message, ModelResponse, StopReason, ToolCall, ToolChoice,
)
from zall.core.safety import RuleSet, SafeLevel
from zall.core.tool import Tool, ToolRegistry, ToolResult


# ──────────────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────────────


class _StreamingAdapter:
    """同时支持 complete 和 complete_stream 的 fake adapter.

    两者returns等价的 ModelResponse (streaming最终 == blocking).
    """

    __test__ = False

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._idx = 0

    @property
    def model_name(self) -> str:
        return "fake-stream"

    def complete(self, messages, tools, tool_choice=ToolChoice.AUTO) -> ModelResponse:
        if self._idx >= len(self._responses):
            return ModelResponse(content="exhausted", stop_reason=StopReason.STOP)
        r = self._responses[self._idx]
        self._idx += 1
        return r

    def complete_stream(self, messages, tools, tool_choice=ToolChoice.AUTO) -> Iterator[tuple[str, ModelResponse]]:
        if self._idx >= len(self._responses):
            yield ("", ModelResponse(content="exhausted", stop_reason=StopReason.STOP))
            return
        r = self._responses[self._idx]
        self._idx += 1
        # 把 content 拆成 token 逐个 yield, 最后 yield 完整 response
        content = r.content
        if content:
            for i in range(0, len(content), 5):
                token = content[i:i+5]
                yield (token, ModelResponse(
                    content=content[:i+5],
                    tool_calls=r.tool_calls if i+5 >= len(content) else (),
                    stop_reason=StopReason.STOP if i+5 < len(content) else r.stop_reason,
                ))
        # 最终 yield 完整 response (含 tool_calls + correctly stop_reason)
        yield ("", r)


class _NonStreamingAdapter:
    """只支持 complete, 不支持 complete_stream 的 fake adapter."""

    __test__ = False

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._idx = 0

    @property
    def model_name(self) -> str:
        return "fake-nostream"

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


def _make_loop(adapter, *, stream=False, observer=None) -> AgentLoop:
    return AgentLoop(
        model=adapter,
        tools=ToolRegistry(tools=(_EchoTool(),)),
        rules=RuleSet(),
        goal=_make_goal(),
        context=Context(user_raw="x", cwd_meta=_CwdMetaStub()),
        user_responder=_AutoAcceptResponder(),
        config=AgentConfig(judge=_MetJudge(), observer=observer, stream=stream),
    )


def _script() -> list[ModelResponse]:
    return [
        ModelResponse(
            content="let me echo",
            tool_calls=(ToolCall(id="tc1", tool_id="echo", args={"text": "hi"}),),
            stop_reason=StopReason.TOOL_USE,
        ),
        ModelResponse(content="done", stop_reason=StopReason.STOP),
    ]


# ──────────────────────────────────────────────────────────────────────────
# 核心invariant: streaming ≡ blocking
# ──────────────────────────────────────────────────────────────────────────


class TestStreamEqualsBlocking:
    def test_stream_and_block_produce_equivalent_egress(self) -> None:
        """Happy path: 同样脚本, stream=True 和 stream=False 产出等价 RunEgress.

        final_state / step_count / tool_calls / model_calls 必须一致.
        streaming只是"怎么调模型"不同, 不改语义.
        """
        # blocking
        loop_block = _make_loop(_StreamingAdapter(_script()), stream=False)
        eg_block = loop_block.run()

        # streaming
        loop_stream = _make_loop(_StreamingAdapter(_script()), stream=True)
        eg_stream = loop_stream.run()

        assert eg_stream.final_state == eg_block.final_state
        assert eg_stream.step_count == eg_block.step_count
        assert eg_stream.total_tool_calls == eg_block.total_tool_calls
        assert eg_stream.total_model_calls == eg_block.total_model_calls
        assert eg_stream.error == eg_block.error

    def test_stream_and_block_equivalent_timeline(self) -> None:
        """Happy path: streaming和blocking的 RunRecorder eventtypeserial一致 (都不含 token)."""
        loop_block = _make_loop(_StreamingAdapter(_script()), stream=False)
        loop_block.run()
        block_kinds = [e.event_type.value for e in loop_block.recorder.events]

        loop_stream = _make_loop(_StreamingAdapter(_script()), stream=True)
        loop_stream.run()
        stream_kinds = [e.event_type.value for e in loop_stream.recorder.events]

        # timeline eventtypeserialmust一致 (token 不进 RunRecorder)
        assert block_kinds == stream_kinds


# ──────────────────────────────────────────────────────────────────────────
# Counterexample: model_token event
# ──────────────────────────────────────────────────────────────────────────


class TestModelTokenEvents:
    def test_stream_emits_model_tokens(self) -> None:
        """Happy path: stream=True 时 observer 收到 model_token event."""
        collected: list[LoopEvent] = []
        loop = _make_loop(
            _StreamingAdapter(_script()), stream=True,
            observer=lambda ev: collected.append(ev),
        )
        loop.run()
        kinds = [e.kind for e in collected]
        assert "model_token" in kinds

    def test_block_does_not_emit_model_tokens(self) -> None:
        """Counterexample: stream=False 时 observer 不收到 model_token event.

        如果blocking模式也发 token 事件, 说明split逻辑有误.
        """
        collected: list[LoopEvent] = []
        loop = _make_loop(
            _StreamingAdapter(_script()), stream=False,
            observer=lambda ev: collected.append(ev),
        )
        loop.run()
        kinds = [e.kind for e in collected]
        assert "model_token" not in kinds

    def test_model_tokens_not_in_recorder(self) -> None:
        """Counterexample: model_token 不进 RunRecorder (token 是呈现层, notaudit轨迹).

        如果 token 进了 timeline, 一次 run 会有几百条 token 事件污染审计轨迹.
        """
        loop = _make_loop(_StreamingAdapter(_script()), stream=True)
        loop.run()
        for ev in loop.recorder.events:
            assert ev.event_type.value != "model_token"


# ──────────────────────────────────────────────────────────────────────────
# Counterexample: adapter 不支持streaming → downgrade
# ──────────────────────────────────────────────────────────────────────────


class TestStreamFallback:
    def test_nostream_adapter_falls_back_to_complete(self) -> None:
        """Counterexample: stream=True 但 adapter 无 complete_stream → downgrade到blocking (does not crash).

        AgentLoop.__init__ 里 self._stream = stream and hasattr(model, 'complete_stream').
        无 complete_stream 的 adapter → self._stream=False → 走blockingbranch.
        """
        collected: list[LoopEvent] = []
        loop = _make_loop(
            _NonStreamingAdapter(_script()), stream=True,  # 要求streaming
            observer=lambda ev: collected.append(ev),
        )
        eg = loop.run()

        # does not crash, 正常完成
        assert eg.final_state == TerminationState.MET
        # downgrade到blocking → 无 model_token event
        kinds = [e.kind for e in collected]
        assert "model_token" not in kinds
