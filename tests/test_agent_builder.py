"""Tests for AgentBuilder fluent builder.

Corresponds to:
  DESIGN.md §4.2 (ToolRegistry)
  DESIGN.md §4.5 (ConfirmGate)
  DESIGN.md §5.2 (Judge)
  DESIGN.md §9.2.1 (Goal confirmation)

IPR-0: each test includes a counterexample.
"""

import pytest


class TestAgentBuilder:
    """AgentBuilder fluent construction tests."""

    def test_builder_creates_loop(self) -> None:
        """Builder with all required fields creates an AgentLoop."""
        from zall.core.builder import AgentBuilder
        from zall.core.loop import AgentLoop

        loop = (AgentBuilder()
                .with_model(_fake_model())
                .with_tools(_fake_tools())
                .with_rules(_fake_rules())
                .with_goal(_fake_goal())
                .with_context(_fake_context())
                .with_responder(_fake_responder())
                .build())
        assert isinstance(loop, AgentLoop)
        loop.finalize()  # Cleanup

    def test_builder_chainable(self) -> None:
        """Each .with_*() should return self for chaining."""
        from zall.core.builder import AgentBuilder
        builder = AgentBuilder()
        result = (builder
                  .with_model(_fake_model())
                  .with_tools(_fake_tools())
                  .with_rules(_fake_rules())
                  .with_goal(_fake_goal())
                  .with_context(_fake_context())
                  .with_responder(_fake_responder()))
        assert result is builder

    # Counterexample: missing required field should raise
    def test_builder_missing_required_raises(self) -> None:
        """Building without required fields should raise ValueError."""
        from zall.core.builder import AgentBuilder
        builder = AgentBuilder()
        # Only set some fields
        builder.with_model(_fake_model())
        with pytest.raises(ValueError, match="required"):
            builder.build()

    # Counterexample: missing all fields should raise
    def test_builder_empty_raises(self) -> None:
        """Building with no fields set should raise ValueError."""
        from zall.core.builder import AgentBuilder
        with pytest.raises(ValueError, match="required"):
            AgentBuilder().build()

    def test_builder_with_optional_judge(self) -> None:
        """Optional .with_judge() should be accepted."""
        from zall.core.builder import AgentBuilder
        loop = (AgentBuilder()
                .with_model(_fake_model())
                .with_tools(_fake_tools())
                .with_rules(_fake_rules())
                .with_goal(_fake_goal())
                .with_context(_fake_context())
                .with_responder(_fake_responder())
                .with_judge(_fake_judge())
                .build())
        assert loop is not None
        loop.finalize()

    def test_builder_with_compactor(self) -> None:
        """Optional .with_compactor() should be accepted."""
        from zall.core.builder import AgentBuilder
        from zall.core.compactor import ModelCompactor
        loop = (AgentBuilder()
                .with_model(_fake_model())
                .with_tools(_fake_tools())
                .with_rules(_fake_rules())
                .with_goal(_fake_goal())
                .with_context(_fake_context())
                .with_responder(_fake_responder())
                .with_compactor(ModelCompactor())
                .build())
        assert loop is not None
        loop.finalize()

    def test_builder_with_plan_mode(self) -> None:
        """Optional .with_plan_mode() should be accepted."""
        from zall.core.builder import AgentBuilder
        loop = (AgentBuilder()
                .with_model(_fake_model())
                .with_tools(_fake_tools())
                .with_rules(_fake_rules())
                .with_goal(_fake_goal())
                .with_context(_fake_context())
                .with_responder(_fake_responder())
                .with_plan_mode(True)
                .build())
        assert loop is not None
        loop.finalize()

    def test_builder_with_extensions(self) -> None:
        """Optional .with_extensions() should be accepted."""
        from zall.core.builder import AgentBuilder
        from zall.core.extension import ExtensionRegistry
        loop = (AgentBuilder()
                .with_model(_fake_model())
                .with_tools(_fake_tools())
                .with_rules(_fake_rules())
                .with_goal(_fake_goal())
                .with_context(_fake_context())
                .with_responder(_fake_responder())
                .with_extensions(ExtensionRegistry())
                .build())
        assert loop is not None
        loop.finalize()

    def test_builder_with_everything(self) -> None:
        """Builder should accept all optional fields."""
        from zall.core.builder import AgentBuilder
        from zall.core.extension import ExtensionRegistry
        from zall.core.compactor import ModelCompactor
        loop = (AgentBuilder()
                .with_model(_fake_model())
                .with_tools(_fake_tools())
                .with_rules(_fake_rules())
                .with_goal(_fake_goal())
                .with_context(_fake_context())
                .with_responder(_fake_responder())
                .with_judge(_fake_judge())
                .with_observer(None)
                .with_max_steps(100)
                .with_stream(True)
                .with_plan_mode(False)
                .with_allow_downgrade(True)
                .with_compactor(ModelCompactor())
                .with_git_protect(None)
                .with_checkpoint(None)
                .with_anchor(None)
                .with_extensions(ExtensionRegistry())
                .build())
        assert loop is not None
        loop.finalize()


# ═══════════════════════════════════════════════════════════════════
# §2  build_loop_minimal convenience function
# ═══════════════════════════════════════════════════════════════════


class TestBuildLoopMinimal:
    """build_loop_minimal convenience function."""

    def test_minimal_creates_loop(self) -> None:
        """build_loop_minimal should create an AgentLoop."""
        from zall.core.builder import build_loop_minimal
        from zall.core.loop import AgentLoop

        loop = build_loop_minimal(
            _fake_model(), _fake_tools(), _fake_rules(),
            _fake_goal(), _fake_context(), _fake_responder(),
        )
        assert isinstance(loop, AgentLoop)
        loop.finalize()

    def test_minimal_with_kwargs(self) -> None:
        """build_loop_minimal should accept optional kwargs."""
        from zall.core.builder import build_loop_minimal
        from zall.core.compactor import ModelCompactor

        loop = build_loop_minimal(
            _fake_model(), _fake_tools(), _fake_rules(),
            _fake_goal(), _fake_context(), _fake_responder(),
            compactor=ModelCompactor(),
            plan_mode=True,
            max_steps=50,
        )
        assert loop is not None
        loop.finalize()

    def test_minimal_unknown_kwarg_no_error(self) -> None:
        """Unknown kwargs should be silently ignored (not raise)."""
        from zall.core.builder import build_loop_minimal

        loop = build_loop_minimal(
            _fake_model(), _fake_tools(), _fake_rules(),
            _fake_goal(), _fake_context(), _fake_responder(),
            unknown_param=42,  # Should not cause error
        )
        assert loop is not None
        loop.finalize()


# ═══════════════════════════════════════════════════════════════════
# Helpers: minimal fake objects
# ═══════════════════════════════════════════════════════════════════


class _FakeModel:
    model_name = "test-model"
    def complete(self, messages, tools=None, tool_choice=None):
        return None


class _FakeTool:
    tool_id = "test_tool"
    schema = {"type": "function", "function": {"name": "test_tool"}}
    def execute(self, args):
        from zall.core.tool import ToolResult
        return ToolResult(success=True, output="ok")


def _fake_model():
    return _FakeModel()


def _fake_tools():
    from zall.core.tool import ToolRegistry
    return ToolRegistry(tools=(_FakeTool(),))


def _fake_rules():
    from zall.core.safety import RuleSet
    return RuleSet()


def _fake_goal():
    from zall.core.goal import GoalTriple, GoalStatement, GoalType, AcceptanceContract
    from zall.core.refiner import _PlaceholderTermination
    return GoalTriple(
        statement=GoalStatement(
            intent="test",
            rewriting="test",
            rewrite_confidence=1.0,
            goal_type=GoalType.UNKNOWN,
            translation_of=("test",),
            added_intent=(),
        ),
        termination=_PlaceholderTermination(exposed_dependency_set=None),
        acceptance=AcceptanceContract(baseline_frozen_at="test"),
    )


def _fake_context():
    from zall.core.context import Context
    from zall.cli.environment import CwdMeta
    return Context(user_raw="test", cwd_meta=CwdMeta())


class _FakeResponder:
    def ask(self, action, judgement):
        from zall.core.gate import UserResponse, UserResponseType
        return UserResponse(response_type=UserResponseType.ACCEPT)


def _fake_responder():
    return _FakeResponder()


class _FakeJudge:
    def __call__(self, evidence):
        from zall.core.goal import TerminationState
        return TerminationState.UNDECIDABLE


def _fake_judge():
    return _FakeJudge()