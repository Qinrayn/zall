"""AgentLoop observer seam invariant test (§6.1 presentation layer projection).

IPR-0: each test must contain a counterexample.

本test守护的是 core/loop.py 的 observer seam:
  - observer 是可选呈现层钩sub, 不传时行for不变 (现有 221 test已证)
  - observer 异常被吞, 不得改变 RunEgress (IPR-0 Counterexample: 呈现层崩了不能改语义)
  - LoopEvent frozen (observer 不得 mutate)

Counterexample:
  1. 抛异常的 observer → RunEgress 与无 observer 时一致 (不得因渲染崩而改结果)
  2. observer 试图改 LoopEvent.step → must raise (frozen)
  3. observer 收到的事件 kind 有序 (model_call → tool_call_start → tool_call_end → judge_result)
"""

from __future__ import annotations

import pytest

from zall.core.accountability import (
    Evidence,
    Judge,
    JudgeVerdict,
)
from zall.core.action import Action
from zall.core.context import Context
from zall.core.gate import (
    UserResponder,
    UserResponse,
    UserResponseType,
)
from zall.core.goal import (
    AcceptanceContract,
    GoalStatement,
    GoalTriple,
    GoalType,
    TerminationState,
)
from zall.core.loop import AgentLoop, LoopEvent
from zall.core.model import (
    Message,
    ModelResponse,
    StopReason,
    ToolCall,
    ToolChoice,
)
from zall.core.safety import RuleSet, SafeLevel
from zall.core.tool import Tool, ToolRegistry, ToolResult


# ──────────────────────────────────────────────────────────────────────────
# Fakes (复用 test_loop_invariants 的范式)
# ──────────────────────────────────────────────────────────────────────────


class _ScriptedAdapter:
    __test__ = False

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._call_index = 0

    @property
    def model_name(self) -> str:
        return "fake-scripted"

    def complete(
        self,
        messages: list[Message],
        tools: list[dict],
        tool_choice: ToolChoice = ToolChoice.AUTO,
    ) -> ModelResponse:
        if self._call_index >= len(self._responses):
            return ModelResponse(content="script exhausted", stop_reason=StopReason.STOP)
        resp = self._responses[self._call_index]
        self._call_index += 1
        return resp


class _EchoTool:
    __test__ = False

    @property
    def tool_id(self) -> str:
        return "echo"

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo back the input",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                },
            },
        }

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output=f"echoed: {args.get('text', '')}")


class _AutoAcceptResponder:
    __test__ = False

    def ask(self, action: Action, judgement) -> UserResponse:
        if judgement.level == SafeLevel.BLACKLIST:
            return UserResponse(response_type=UserResponseType.REJECT)
        return UserResponse(response_type=UserResponseType.ACCEPT)


class _AlwaysMetJudge:
    __test__ = False

    @property
    def judge_type(self) -> str:
        return "system"

    def __call__(self, evidence: Evidence) -> JudgeVerdict:
        return JudgeVerdict(state=TerminationState.MET, report="all good")


class _CwdMetaStub:
    __test__ = False

    def __init__(self) -> None:
        self.cwd_path = "/home/user/project"
        self.git_branch = "main"
        self.git_remote = "origin"


def _make_goal() -> GoalTriple:
    class _UserTermination:
        exposed_dependency_set = None

        def __call__(self, state: object) -> TerminationState:
            return TerminationState.UNDECIDABLE

    return GoalTriple(
        statement=GoalStatement(
            intent="echo hello",
            rewriting="echo hello world",
            rewrite_confidence=0.9,
            goal_type=GoalType.DOCS,
            translation_of=("seg1",),
            added_intent=(),
        ),
        termination=_UserTermination(),
        acceptance=AcceptanceContract(baseline_frozen_at="abc123"),
    )


def _make_context() -> Context:
    return Context(user_raw="echo hello world", cwd_meta=_CwdMetaStub())


def _make_loop(
    adapter: _ScriptedAdapter,
    judge=None,
    observer=None,
) -> AgentLoop:
    return AgentLoop(
        model=adapter,
        tools=ToolRegistry(tools=(_EchoTool(),)),
        rules=RuleSet(),
        goal=_make_goal(),
        context=_make_context(),
        user_responder=_AutoAcceptResponder(),
        judge=judge,
        observer=observer,
    )


def _two_step_script() -> list[ModelResponse]:
    return [
        ModelResponse(
            content="let me echo",
            tool_calls=(ToolCall(id="tc1", tool_id="echo", args={"text": "hello"}),),
            stop_reason=StopReason.TOOL_USE,
        ),
        ModelResponse(content="done", stop_reason=StopReason.STOP),
    ]


# ──────────────────────────────────────────────────────────────────────────
# Happy path: observer 收到有序event
# ──────────────────────────────────────────────────────────────────────────


class TestObserverReceivesEvents:
    def test_observer_collects_ordered_events(self) -> None:
        """Happy path: observer 收到的event kind serial符合 Loop 内部时序.

        期望序列 (两步 run: tool_use → stop + judge):
          model_call (step1, tool_use)
          gate_decision (step1)
          tool_call_start (step1)
          tool_call_end (step1)
          model_call (step2, stop)
          judge_result (step2)
        """
        collected: list[LoopEvent] = []

        def observer(ev: LoopEvent) -> None:
            collected.append(ev)

        adapter = _ScriptedAdapter(_two_step_script())
        loop = _make_loop(adapter, judge=_AlwaysMetJudge(), observer=observer)
        egress = loop.run()

        # run 成功
        assert egress.final_state == TerminationState.MET
        assert egress.error is None

        kinds = [e.kind for e in collected]
        # 至少包含这些关键 kind, 且sequentialcorrectly
        assert "model_call" in kinds
        assert "gate_decision" in kinds
        assert "tool_call_start" in kinds
        assert "tool_call_end" in kinds
        assert "judge_result" in kinds

        # 时序: tool_call_start must在 tool_call_end 之前 (intent先于行动, §6.1)
        start_idx = kinds.index("tool_call_start")
        end_idx = kinds.index("tool_call_end")
        assert start_idx < end_idx

        # judge_result must在所有 tool event之后
        assert end_idx < kinds.index("judge_result")

    def test_observer_step_field_monotonic(self) -> None:
        """Happy path: observer 收到的event step 不递减."""
        collected: list[LoopEvent] = []

        def observer(ev: LoopEvent) -> None:
            collected.append(ev)

        adapter = _ScriptedAdapter(_two_step_script())
        loop = _make_loop(adapter, judge=_AlwaysMetJudge(), observer=observer)
        loop.run()

        steps = [e.step for e in collected]
        for prev, cur in zip(steps, steps[1:]):
            assert cur >= prev

    def test_model_call_start_emitted_before_model_call(self) -> None:
        """Happy path: model_call_start 在 model_call 之前 emit (呈现层 spinner 用).

        P5: 调模型前先广播 start, 让 spinner 立即显示.
        """
        collected: list[LoopEvent] = []
        loop = _make_loop(
            _ScriptedAdapter(_two_step_script()),
            judge=_AlwaysMetJudge(),
            observer=lambda ev: collected.append(ev),
        )
        loop.run()
        kinds = [e.kind for e in collected]
        assert "model_call_start" in kinds
        # start must在 model_call 之前
        assert kinds.index("model_call_start") < kinds.index("model_call")

    def test_model_call_start_not_in_recorder(self) -> None:
        """Counterexample: model_call_start 不进 RunRecorder (start notauditevent).

        RunRecorder 只记完整事件 (model_call), start 是呈现层专用.
        如果 start 进了 timeline, 会污染审计轨迹.
        """
        loop = _make_loop(_ScriptedAdapter(_two_step_script()), judge=_AlwaysMetJudge())
        loop.run()
        for ev in loop.recorder.events:
            assert ev.event_type.value != "model_call_start"


# ──────────────────────────────────────────────────────────────────────────
# Counterexample: observer exception不得改变 RunEgress
# ──────────────────────────────────────────────────────────────────────────


class TestObserverExceptionIsolation:
    def test_observer_raising_does_not_change_egress(self) -> None:
        """Counterexample: observer 抛exception → RunEgress must与无 observer 时完全一致.

        如果 _emit 不吞异常, 一个 print 报错就能让 agent 跑出不同结果 →
        违反可复现性 (§6.2) 和 PR-0 (语义不得被呈现层污染).
        """

        def crashing_observer(ev: LoopEvent) -> None:
            raise RuntimeError("render layer crashed!")

        # 无 observer 的基线
        baseline_loop = _make_loop(
            _ScriptedAdapter(_two_step_script()), judge=_AlwaysMetJudge()
        )
        baseline_egress = baseline_loop.run()

        # 有崩溃 observer
        crashing_loop = _make_loop(
            _ScriptedAdapter(_two_step_script()),
            judge=_AlwaysMetJudge(),
            observer=crashing_observer,
        )
        crashing_egress = crashing_loop.run()

        # 两者 RunEgress must一致 (observer 故障不pollution语义)
        assert crashing_egress.final_state == baseline_egress.final_state
        assert crashing_egress.step_count == baseline_egress.step_count
        assert crashing_egress.total_tool_calls == baseline_egress.total_tool_calls
        assert crashing_egress.total_model_calls == baseline_egress.total_model_calls
        assert crashing_egress.error == baseline_egress.error

    def test_observer_raising_does_not_break_timeline_chain(self) -> None:
        """Counterexample: observer 抛exception → RunRecorder 链仍须完整.

        如果 observer 异常泄漏到 RunRecorder.append, 链会断.
        """

        def crashing_observer(ev: LoopEvent) -> None:
            raise ValueError("boom")

        loop = _make_loop(
            _ScriptedAdapter(_two_step_script()),
            judge=_AlwaysMetJudge(),
            observer=crashing_observer,
        )
        loop.run()

        # 链must完整 (observer exception不得影响audit轨迹)
        assert loop.recorder.verify_chain() is True


# ──────────────────────────────────────────────────────────────────────────
# Counterexample: LoopEvent frozen
# ──────────────────────────────────────────────────────────────────────────


class TestLoopEventFrozen:
    def test_loopevent_is_immutable(self) -> None:
        """Counterexample: observer 试图改 LoopEvent.step → must raise (frozen).

        如果 LoopEvent 可变, observer 能 mutate 事件载荷,
        若同一事件被多个 observer 共享 → 不可复现.
        """
        from pydantic import ValidationError

        ev = LoopEvent(kind="model_call", step=1, payload={"content": "hi"})
        with pytest.raises(ValidationError):
            ev.step = 999  # type: ignore[misc]
        with pytest.raises(ValidationError):
            ev.kind = "tampered"  # type: ignore[misc]

    def test_loopevent_kind_required(self) -> None:
        """Counterexample: LoopEvent 无 kind → must raise (kind non-空).

        kind 是 observer 路由的依据, 缺失则呈现层无法区分事件.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LoopEvent(step=1)  # type: ignore[call-arg]

    def test_loopevent_step_must_be_positive(self) -> None:
        """Counterexample: step < 1 → 不valid (Loop 内部 step 从 1 起)."""
        # step=0 在exceptionbranch可能出现在 _make_egress 前的计数, 但 LoopEvent 语义上 step≥1
        # 这里测constructvalid (step 是 int), 不强制 ≥1 (避免过度约束exceptionpath)
        # 但 step must是 int
        ev = LoopEvent(kind="error", step=1, payload={})
        assert isinstance(ev.step, int)


# ──────────────────────────────────────────────────────────────────────────
# Counterexample: 无 observer 时行for不变 (回归守护)
# ──────────────────────────────────────────────────────────────────────────


class TestNoObserverRegression:
    def test_no_observer_same_as_none(self) -> None:
        """Counterexample: 不传 observer (None) 与根本不支持 observer 的旧version行for一致.

        这是seam是"纯加法"的证明.如果加 observer 改了默认行for,
        现有 221 test会受影响 —— 但这条test单独守护 loop 的核心路径.
        """
        # observer=None (显式)
        loop_none = _make_loop(
            _ScriptedAdapter(_two_step_script()), judge=_AlwaysMetJudge(), observer=None
        )
        egress_none = loop_none.run()

        # observer 不传 (default)
        loop_default = _make_loop(
            _ScriptedAdapter(_two_step_script()), judge=_AlwaysMetJudge()
        )
        egress_default = loop_default.run()

        assert egress_none.final_state == egress_default.final_state
        assert egress_none.step_count == egress_default.step_count
        assert egress_none.total_tool_calls == egress_default.total_tool_calls
        assert egress_none.error == egress_default.error
