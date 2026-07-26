"""§10 I-0 + I-7 六维本体论完整性不变量 (MASTER.md §1.2 + §10)。

I-0 六维完整性: 任何 AgentLoop 实例必须实现全部六维组件
    (Identity/Commitment/Perception/Authority/Accountability/Verifiability)。
    缺一维不是 agent, 是工具 (§1.2)。
I-7 分层严谨: 核心恰好暴露 6 个维度, 不多不少。

这两条是六维本体论的"旗舰不变量", 此前无测试 (违反 IPR-0)。本文件补齐,
每个测试含 counterexample (IPR-0)。

对应 MASTER.md 附录 B.4: I-0 / I-7。
"""

from __future__ import annotations

from zall.core.action import Action
from zall.core.agent import AgentIdentity
from zall.core.context import Context
from zall.core.gate import UserResponder, UserResponse, UserResponseType
from zall.core.goal import (
    AcceptanceContract,
    GoalStatement,
    GoalTriple,
    GoalType,
    TerminationState,
)
from zall.core.loop import (
    ONTOLOGY_DIMENSIONS,
    AgentLoop,
    agent_has_all_dimensions,
)
from zall.core.loop_config import AgentConfig
from zall.core.model import (
    Message,
    ModelResponse,
    StopReason,
    ToolChoice,
)
from zall.core.safety import RuleSet, SafeLevel
from zall.core.tool import ToolRegistry, ToolResult


# ──────────────────────────────────────────────────────────────────────────
# Fakes (minimal, __test__ = False 防 pytest 误收)
# ──────────────────────────────────────────────────────────────────────────


class _ScriptedAdapter:
    __test__ = False

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._i = 0

    @property
    def model_name(self) -> str:
        return "fake-scripted"

    def complete(self, messages, tools, tool_choice: ToolChoice = ToolChoice.AUTO):  # type: ignore[no-untyped-def]
        if self._i >= len(self._responses):
            return ModelResponse(content="done", stop_reason=StopReason.STOP)
        resp = self._responses[self._i]
        self._i += 1
        return resp


class _EchoTool:
    __test__ = False

    @property
    def tool_id(self) -> str:
        return "echo"

    @property
    def schema(self) -> dict:  # type: ignore[type-arg]
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

    def execute(self, args: dict) -> ToolResult:  # type: ignore[type-arg]
        return ToolResult(success=True, output="ok")


class _AutoAcceptResponder:
    __test__ = False

    def ask(self, action: Action, judgement) -> UserResponse:  # type: ignore[no-untyped-def]
        if judgement.level == SafeLevel.BLACKLIST:
            return UserResponse(response_type=UserResponseType.REJECT)
        return UserResponse(response_type=UserResponseType.ACCEPT)


class _CwdMetaStub:
    __test__ = False

    def __init__(self) -> None:
        self.cwd_path = "."
        self.git_branch = "main"
        self.git_remote = "origin"


def _make_goal() -> GoalTriple:
    class _Termination:
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
        termination=_Termination(),
        acceptance=AcceptanceContract(baseline_frozen_at="abc123"),
    )


def _make_loop(config: AgentConfig | None = None) -> AgentLoop:
    return AgentLoop(
        model=_ScriptedAdapter([]),
        tools=ToolRegistry(tools=(_EchoTool(),)),
        rules=RuleSet(),
        goal=_make_goal(),
        context=Context(user_raw="echo hello world", cwd_meta=_CwdMetaStub()),
        user_responder=_AutoAcceptResponder(),
        config=config or AgentConfig(),
    )


# ──────────────────────────────────────────────────────────────────────────
# I-0 六维完整性 (Ontological Completeness)
# ──────────────────────────────────────────────────────────────────────────


class TestOntologicalCompleteness:
    """I-0: AgentLoop 必须实现全部六维组件。"""

    def test_agent_loop_has_all_six_dimensions(self) -> None:
        """Happy path: 构造的 AgentLoop 六维齐备。

        Counterexample: 若任一维度属性缺失或强制维度为 None,
        agent_has_all_dimensions 返回 False (见下面两个反例测试)。
        """
        loop = _make_loop()
        assert agent_has_all_dimensions(loop) is True
        # 六维属性槽位都存在
        for dim in ONTOLOGY_DIMENSIONS:
            assert hasattr(loop, dim), f"缺少维度属性: {dim}"
        # 四个强制维度非 None
        assert loop.identity is not None
        assert loop.commitment is not None
        assert loop.authority is not None
        assert loop.verifiability is not None

    def test_identity_always_present_even_without_config(self) -> None:
        """反例保护: 未显式提供 identity 也必须有默认身份。

        Counterexample: 若 Identity 未接入 (旧行为), loop.identity 不存在或为 None,
        六维完整性被破坏 → agent 退化为工具 (§1.2 缺①)。
        """
        loop = _make_loop(AgentConfig())  # 无 identity
        assert loop.identity is not None
        assert isinstance(loop.identity, AgentIdentity)
        assert loop.identity.agent_id  # 非空 agent_id 用于归因

    def test_explicit_identity_is_used(self) -> None:
        """Happy path: 显式提供的 Identity 被 loop 采用。"""
        ident = AgentIdentity(agent_id="agent-42", name="tester")
        loop = _make_loop(AgentConfig(identity=ident))
        assert loop.identity.agent_id == "agent-42"
        assert loop.identity.name == "tester"

    def test_counterexample_object_missing_dimension(self) -> None:
        """Counterexample: 缺 verifiability 维度的对象不是 agent。"""

        class _FiveDims:
            identity = object()
            commitment = object()
            perception_engine = None
            authority = object()
            accountability = None
            # 故意缺 verifiability

        assert agent_has_all_dimensions(_FiveDims()) is False

    def test_counterexample_mandatory_dimension_none(self) -> None:
        """Counterexample: 强制维度 (identity) 为 None → 退化为工具, 非 agent。"""

        class _NullIdentity:
            identity = None  # 强制维度不可为 None
            commitment = object()
            perception_engine = None
            authority = object()
            accountability = None
            verifiability = object()

        assert agent_has_all_dimensions(_NullIdentity()) is False


# ──────────────────────────────────────────────────────────────────────────
# I-7 分层严谨: 核心恰好 6 维
# ──────────────────────────────────────────────────────────────────────────


class TestTieredRigorSixDimensions:
    """I-7: 核心恰好暴露 6 个维度, 不多不少。"""

    def test_exactly_six_dimensions(self) -> None:
        """Happy path: 本体论维度恰好 6 个。

        Counterexample: 若新增第 7 维或漏掉某维, 此断言 fail
        (守住 §1.3 "Learning 为何不是第七维" 的结论)。
        """
        assert len(ONTOLOGY_DIMENSIONS) == 6
        assert set(ONTOLOGY_DIMENSIONS) == {
            "identity",
            "commitment",
            "perception_engine",
            "authority",
            "accountability",
            "verifiability",
        }

    def test_optional_dimensions_may_be_none(self) -> None:
        """Perception/Accountability 为可选维度, 允许 None 但仍是合法 agent。

        Counterexample: 若把可选维度也当强制 (要求非 None),
        Q&A 场景 (无 judge/无感知) 会被误判为"非 agent"。
        """
        loop = _make_loop(AgentConfig())  # 无 judge, 无 perception
        assert loop.perception_engine is None
        assert loop.accountability is None
        # 仍然六维完整 (可选维度允许 None)
        assert agent_has_all_dimensions(loop) is True
