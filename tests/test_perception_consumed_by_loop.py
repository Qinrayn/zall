"""Invariant tests: perception consumed by loop (MASTER.md §12.3 E1).

§12.3 E1.1: anomaly detected -> timeline event + system nudge
§12.3 E1.2: state change detected -> system summary injection
§12.3 E1.3: invariant tests for above (this file)

IPR-0: each test must contain a counterexample.
"""

from __future__ import annotations

import pytest

from zall.core.accountability import (
    AccountabilityResult,
    CaveatType,
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
from zall.core.loop import AgentLoop
from zall.core.loop_config import AgentConfig
from zall.core.model import (
    Message,
    ModelAdapter,
    ModelResponse,
    StopReason,
    ToolCall,
    ToolChoice,
)
from zall.core.safety import RuleSet, SafeLevel
from zall.core.tool import Tool, ToolRegistry, ToolResult
from zall.core.verifiability import EventType
from zall.core.perception.sensor import StateEstimate, Observation


# ──────────────────────────────────────────────────────────────────────────
# Fake perception engine (§12.3 E1)
# ──────────────────────────────────────────────────────────────────────────


class _FakePerceptionEngine:
    """Controllable fake perception engine for testing loop consumption.

    __test__ = False 防 pytest 误收.
    """

    __test__ = False

    def __init__(
        self,
        state_estimate: StateEstimate | None = None,
        anomaly: bool = False,
    ) -> None:
        self._state_estimate = state_estimate or StateEstimate(
            state={},
            confidence=0.0,
            sources=["fake"],
            timestamp=1000000,
        )
        self._anomaly = anomaly
        self._predict_called = False

    def perceive(self) -> StateEstimate:
        return self._state_estimate

    def anomaly(self) -> bool:
        return self._anomaly

    def predict(self, action: Action) -> StateEstimate:
        self._predict_called = True
        return self._state_estimate


class _ScriptedAdapter:
    """按脚本返回预设 ModelResponse 的 fake adapter.

    __test__ = False 防 pytest 误收.
    """

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
    """fake echo tool."""

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
        text = args.get("text", "")
        return ToolResult(success=True, output=f"echoed: {text}")


class _AutoAcceptResponder:
    """fake user responder, 自动 ACCEPT."""

    __test__ = False

    def ask(self, action: Action, judgement) -> UserResponse:
        if judgement.level == SafeLevel.BLACKLIST:
            return UserResponse(response_type=UserResponseType.REJECT)
        return UserResponse(response_type=UserResponseType.ACCEPT)


class _AlwaysMetJudge:
    """fake Judge, 永远返回 met."""

    __test__ = False

    @property
    def judge_type(self) -> str:
        return "system"

    def __call__(self, evidence: Evidence) -> JudgeVerdict:
        return JudgeVerdict(state=TerminationState.MET, report="all good")


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


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
    perception_engine: _FakePerceptionEngine | None = None,
) -> AgentLoop:
    return AgentLoop(
        model=adapter,
        tools=ToolRegistry(tools=(_EchoTool(),)),
        rules=RuleSet(),
        goal=_make_goal(),
        context=_make_context(),
        user_responder=_AutoAcceptResponder(),
        config=AgentConfig(
            judge=_AlwaysMetJudge(),
            perception_engine=perception_engine,
        ),
    )


def _make_state_estimate(
    state: dict | None = None,
    confidence: float = 0.9,
    sources: list[str] | None = None,
) -> StateEstimate:
    return StateEstimate(
        state=state or {},
        confidence=confidence,
        sources=sources or ["fake"],
        timestamp=1000000,
    )


# ──────────────────────────────────────────────────────────────────────────
# §12.3 E1.1: Anomaly circuit breaker
# ──────────────────────────────────────────────────────────────────────────


class TestAnomalyCircuitBreaker:
    """§12.3 E1.1: 感知异常触发 timeline 事件 + system nudge."""

    def test_anomaly_triggers_timeline_event_and_system_nudge(self) -> None:
        """Happy path: anomaly=True -> timeline 有 perception_anomaly 事件 且 messages 出现 system 提示.

        Counterexample: 如果 anomaly 不触发 nudge, 感知异常静默丢失.
        """
        state = _make_state_estimate(state={"git_modified": 3}, confidence=0.5)
        engine = _FakePerceptionEngine(state_estimate=state, anomaly=True)
        adapter = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, perception_engine=engine)
        egress = loop.run()

        # timeline 有 perception_anomaly 事件
        events = loop.recorder.events
        anomaly_events = [e for e in events if e.event_type == EventType.PERCEPTION_ANOMALY]
        assert len(anomaly_events) >= 1, (
            "Counterexample: anomaly=True 但 timeline 无 perception_anomaly 事件"
        )

        # 有 system 消息包含 anomaly 提示
        system_msgs = [m for m in loop.messages if m.role == "system"]
        anomaly_nudges = [m for m in system_msgs if "perception anomaly" in (m.content or "")]
        assert len(anomaly_nudges) >= 1, (
            "Counterexample: anomaly=True 但 messages 无 anomaly system nudge"
        )

    def test_no_anomaly_no_nudge(self) -> None:
        """Counterexample: anomaly=False 时无注入 (不应有误报)."""
        state = _make_state_estimate(state={"git_modified": 0}, confidence=0.9)
        engine = _FakePerceptionEngine(state_estimate=state, anomaly=False)
        adapter = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, perception_engine=engine)
        loop.run()

        events = loop.recorder.events
        anomaly_events = [e for e in events if e.event_type == EventType.PERCEPTION_ANOMALY]
        assert len(anomaly_events) == 0, (
            "Counterexample: anomaly=False 但 timeline 仍有 perception_anomaly 事件"
        )

        system_msgs = [m for m in loop.messages if m.role == "system"]
        anomaly_nudges = [m for m in system_msgs if "perception anomaly" in (m.content or "")]
        assert len(anomaly_nudges) == 0, (
            "Counterexample: anomaly=False 但 messages 有 anomaly system nudge"
        )


# ──────────────────────────────────────────────────────────────────────────
# §12.3 E1.2: State change summary injection
# ──────────────────────────────────────────────────────────────────────────


class TestStateChangeSummary:
    """§12.3 E1.2: 感知状态变化时注入紧凑摘要."""

    def test_state_change_triggers_summary(self) -> None:
        """Happy path: git modified 文件数变化时注入摘要.

        C1 fix: 状态键使用传感器真实输出 "modified" (列表) / "errors" (int),
        而非旧的 "git_modified"/"lsp_errors" (no sensor writes those keys).

        Counterexample: 如果状态变化不注入 system 消息, 模型不知道环境变化.
        """
        # 第一帧: 无变化
        state1 = _make_state_estimate(
            state={"modified": [], "errors": 0}, confidence=0.9
        )
        engine1 = _FakePerceptionEngine(state_estimate=state1, anomaly=False)
        adapter1 = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop1 = _make_loop(adapter1, perception_engine=engine1)
        loop1.run()
        # 第一帧不应有变化注入 (prev 为 None 时跳过)
        system_msgs1 = [m for m in loop1.messages if m.role == "system"]
        change_nudges1 = [m for m in system_msgs1 if "perception state" in (m.content or "")]
        assert len(change_nudges1) == 0, (
            "第一帧 (prev=None) 不应注入变化摘要"
        )

        # 第二帧: modified 文件数变化 (3 个文件)
        state2 = _make_state_estimate(
            state={"modified": ["a.py", "b.py", "c.py"], "errors": 0}, confidence=0.9
        )
        engine2 = _FakePerceptionEngine(state_estimate=state2, anomaly=False)
        adapter2 = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop2 = _make_loop(adapter2, perception_engine=engine2)
        # 手动设置 prev_snapshot 模拟第一帧结果 (C1: 用 modified_count 键)
        loop2._prev_perception_snapshot = {
            "modified_count": 0,
            "lsp_errors": 0,
            "confidence": 0.9,
        }
        loop2.run()

        system_msgs2 = [m for m in loop2.messages if m.role == "system"]
        change_nudges2 = [m for m in system_msgs2 if "perception state" in (m.content or "")]
        assert len(change_nudges2) >= 1, (
            "Counterexample: modified 文件数变化但无 system 摘要注入"
        )
        # 验证摘要内容包含变化描述
        assert "git_modified" in (change_nudges2[0].content or ""), (
            "摘要应包含 git_modified 变化描述"
        )

    def test_no_state_change_no_summary(self) -> None:
        """Counterexample: 状态不变时不应注入摘要 (避免冗许)."""
        state = _make_state_estimate(
            state={"modified": [], "errors": 0}, confidence=0.9
        )
        engine = _FakePerceptionEngine(state_estimate=state, anomaly=False)
        adapter = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, perception_engine=engine)
        # 设置与当前状态相同的 prev_snapshot (C1: modified_count=0 匹配空列表)
        loop._prev_perception_snapshot = {
            "modified_count": 0,
            "lsp_errors": 0,
            "confidence": 0.9,
        }
        loop.run()

        system_msgs = [m for m in loop.messages if m.role == "system"]
        change_nudges = [m for m in system_msgs if "perception state" in (m.content or "")]
        assert len(change_nudges) == 0, (
            "Counterexample: 状态不变但仍有 system 摘要注入"
        )


# ──────────────────────────────────────────────────────────────────────────
# §12.3 E1.4: v1.1 脏工作区基线 — anomaly 不应因启动时脏工作区触发
# ──────────────────────────────────────────────────────────────────────────


class TestBaselineAnomalyV1_1:
    """v1.1: anomaly 检测应使用基线, 忽略启动时脏工作区状态."""

    def test_high_baseline_no_new_changes_no_anomaly(self) -> None:
        """Happy path: baseline 高 (72 files) + 无新增 → 不触发 anomaly.
        
        Counterexample: 旧行为 baseline 高时触发 anomaly, 误导用户认为 zall 导致了问题.
        """
        state = _make_state_estimate(
            state={"modified": ["a"] * 72, "modified_files": ["a"] * 72},
            confidence=0.9,
        )
        engine = _FakePerceptionEngine(state_estimate=state, anomaly=False)
        adapter = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, perception_engine=engine)
        # 设置基线为 72, 模拟启动时工作区已有 72 个修改文件
        loop._baseline_modified_files = 72
        if loop._perception_engine is not None:
            wm = getattr(loop._perception_engine, 'world_model', None)
            if wm is not None and hasattr(wm, 'set_baseline_modified'):
                wm.set_baseline_modified(72)
        loop.run()

        events = loop.recorder.events
        anomaly_events = [e for e in events if e.event_type == EventType.PERCEPTION_ANOMALY]
        assert len(anomaly_events) == 0, (
            "Counterexample: baseline 高但无新增, 不应触发 anomaly"
        )

    def test_low_baseline_with_new_changes_triggers_anomaly(self) -> None:
        """Happy path: baseline 低 (0) + run 期间新增 > 50 files → 触发 anomaly.
        
        反例: 如果 baseline 低但新增 51+ files 不触发, 则 anomaly 检测失效.
        """
        state = _make_state_estimate(
            state={"modified": ["a"] * 51, "modified_files": ["a"] * 51},
            confidence=0.9,
        )
        engine = _FakePerceptionEngine(state_estimate=state, anomaly=True)
        adapter = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, perception_engine=engine)
        # 基线为 0, 当前 51 > 50 → 触发 anomaly
        loop._baseline_modified_files = 0
        if loop._perception_engine is not None:
            wm = getattr(loop._perception_engine, 'world_model', None)
            if wm is not None and hasattr(wm, 'set_baseline_modified'):
                wm.set_baseline_modified(0)
        loop.run()

        events = loop.recorder.events
        anomaly_events = [e for e in events if e.event_type == EventType.PERCEPTION_ANOMALY]
        assert len(anomaly_events) >= 1, (
            "Counterexample: baseline 低 + 新增 51 files, 但未触发 anomaly"
        )