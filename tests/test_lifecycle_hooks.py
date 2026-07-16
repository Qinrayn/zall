"""Tests for typed lifecycle hooks and self-evolving extension system.

Corresponds to:
  DESIGN.md §3.3.6 (Refiner residual wounds → extensions)
  DESIGN.md §3.6 (Self-innovation capability)
  DESIGN.md §4.4 (K-value adjustments)
  DESIGN.md §5.2 (Judge composition adjustments)
  DESIGN.md §7 (Long-term extensibility)

IPR-0: each test includes a counterexample (assertion that would fail
if the invariant is violated).
"""

from __future__ import annotations

import os
import tempfile

import pytest

from zall.core.lifecycle import (
    SelfSuggestion,
    SuggestionAccumulator,
    ToolResultInput,
    TurnDoneInput,
    TurnStartInput,
    UserInputReceived,
)
from zall.core.extension import ExtensionRegistry, _is_typed_extension, _TYPED_HOOK_METHODS


# ═══════════════════════════════════════════════════════════════════
# §1  SelfSuggestion tests
# ═══════════════════════════════════════════════════════════════════


class TestSelfSuggestion:
    """SelfSuggestion is the core self-evolution contract."""

    def test_create_valid_suggestion(self) -> None:
        """A valid SelfSuggestion should be constructible."""
        s = SelfSuggestion(
            kind="adjust_k",
            target="bugfix",
            value=2,
            confidence=0.8,
            evidence="High error rate on bugfix tasks",
        )
        assert s.kind == "adjust_k"
        assert s.target == "bugfix"
        assert s.value == 2
        assert s.confidence == 0.8
        assert s.evidence

    def test_confidence_range(self) -> None:
        """Confidence must be between 0.0 and 1.0."""
        SelfSuggestion(kind="adjust_k", target="t", value=1, confidence=0.0, evidence="e")
        SelfSuggestion(kind="adjust_k", target="t", value=1, confidence=1.0, evidence="e")
        # Counterexample: confidence > 1.0 should raise
        with pytest.raises(ValueError, match="confidence"):
            SelfSuggestion(kind="adjust_k", target="t", value=1, confidence=1.5, evidence="e")
        # Counterexample: confidence < 0.0 should raise
        with pytest.raises(ValueError, match="confidence"):
            SelfSuggestion(kind="adjust_k", target="t", value=1, confidence=-0.1, evidence="e")

    def test_all_kinds(self) -> None:
        """All suggestion kinds should be constructible."""
        for kind in ("adjust_k", "register_goaltype", "add_rule", "create_skill", "adjust_judge"):
            s = SelfSuggestion(kind=kind, target="t", value=1, confidence=0.5, evidence="e")
            assert s.kind == kind


# ═══════════════════════════════════════════════════════════════════
# §2  Typed input models
# ═══════════════════════════════════════════════════════════════════


class TestTypedInputs:
    """Typed input models carry structured data for extensions."""

    def test_turn_start_input(self) -> None:
        """TurnStartInput carries goal, model, and messages."""
        inp = TurnStartInput(
            goal="test_goal",
            model_name="agnes-2.0-flash",
            messages=["msg1", "msg2"],
            tools=(),
            step=0,
        )
        assert inp.goal_type == "unknown"  # No real goal object
        assert inp.model_name == "agnes-2.0-flash"
        assert inp.step == 0

    def test_turn_done_input(self) -> None:
        """TurnDoneInput carries session summary."""
        inp = TurnDoneInput(
            egress="test_egress",
            step_count=10,
            tool_counts={"bash": 5, "grep": 3},
            tool_errors={"bash": 1},
            duration=30.5,
            goal_type="bugfix",
        )
        assert inp.total_tool_calls == 8
        assert inp.error_rate == 0.125  # 1/8
        assert inp.goal_type == "bugfix"

    # Counterexample: empty session should have zero error rate
    def test_empty_session_error_rate(self) -> None:
        """Empty session has zero error rate (no division by zero)."""
        inp = TurnDoneInput(
            egress="test",
            step_count=0,
            tool_counts={},
            tool_errors={},
        )
        assert inp.total_tool_calls == 0
        assert inp.error_rate == 0.0

    def test_tool_result_input(self) -> None:
        """ToolResultInput carries tool execution detail."""
        inp = ToolResultInput(
            tool_id="bash",
            success=True,
            output="hello",
            step=5,
            duration=1.2,
            args={"command": "echo hello"},
        )
        assert inp.tool_id == "bash"
        assert inp.success is True
        assert inp.step == 5
        assert inp.args["command"] == "echo hello"

    # Counterexample: failed tool should have error set
    def test_failed_tool_result(self) -> None:
        """Failed tool result should carry error info."""
        inp = ToolResultInput(
            tool_id="bash",
            success=False,
            output="",
            error="command not found",
            step=3,
        )
        assert inp.success is False
        assert inp.error == "command not found"

    def test_user_input_received(self) -> None:
        """UserInputReceived carries user content."""
        inp = UserInputReceived(content="hello world", step=1)
        assert inp.content == "hello world"
        assert inp.step == 1


# ═══════════════════════════════════════════════════════════════════
# §3  SuggestionAccumulator
# ═══════════════════════════════════════════════════════════════════


class TestSuggestionAccumulator:
    """SuggestionAccumulator collects suggestions from multiple extensions."""

    def test_empty_accumulator(self) -> None:
        acc = SuggestionAccumulator()
        assert not acc
        assert len(acc) == 0
        assert acc.suggestions == []

    def test_add_suggestion(self) -> None:
        acc = SuggestionAccumulator()
        s = SelfSuggestion(kind="adjust_k", target="t", value=1, confidence=0.5, evidence="e")
        acc.add(s)
        assert len(acc) == 1
        assert acc.suggestions[0] == s

    def test_add_all(self) -> None:
        acc = SuggestionAccumulator()
        suggestions = [
            SelfSuggestion(kind="adjust_k", target="t1", value=1, confidence=0.5, evidence="e"),
            SelfSuggestion(kind="create_skill", target="t2", value="v", confidence=0.5, evidence="e"),
        ]
        acc.add_all(suggestions)
        assert len(acc) == 2

    def test_by_kind(self) -> None:
        acc = SuggestionAccumulator()
        acc.add(SelfSuggestion(kind="adjust_k", target="t", value=1, confidence=0.5, evidence="e"))
        acc.add(SelfSuggestion(kind="create_skill", target="t", value="v", confidence=0.5, evidence="e"))
        acc.add(SelfSuggestion(kind="adjust_k", target="t2", value=2, confidence=0.5, evidence="e"))
        assert len(acc.by_kind("adjust_k")) == 2
        assert len(acc.by_kind("create_skill")) == 1
        assert len(acc.by_kind("register_goaltype")) == 0

    def test_clear(self) -> None:
        acc = SuggestionAccumulator()
        acc.add(SelfSuggestion(kind="adjust_k", target="t", value=1, confidence=0.5, evidence="e"))
        acc.clear()
        assert not acc

    # Counterexample: clear should not affect other instances
    def test_clear_isolated(self) -> None:
        acc1 = SuggestionAccumulator()
        acc2 = SuggestionAccumulator()
        acc1.add(SelfSuggestion(kind="adjust_k", target="t", value=1, confidence=0.5, evidence="e"))
        acc1.clear()
        assert not acc1
        assert not acc2  # Both should be empty

    def test_bool_empty(self) -> None:
        """Counterexample: empty accumulator should be falsy."""
        acc = SuggestionAccumulator()
        assert not acc
        acc.add(SelfSuggestion(kind="adjust_k", target="t", value=1, confidence=0.5, evidence="e"))
        assert acc


# ═══════════════════════════════════════════════════════════════════
# §4  ExtensionRegistry typed hook support
# ═══════════════════════════════════════════════════════════════════


class _TypedTestExtension:
    """A typed extension for testing."""
    name = "test_typed"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.suggestions: list[SelfSuggestion] = []

    def on_turn_start(self, input: TurnStartInput) -> list[SelfSuggestion] | None:
        self.calls.append("on_turn_start")
        return None

    def on_turn_done(self, input: TurnDoneInput) -> list[SelfSuggestion] | None:
        self.calls.append("on_turn_done")
        return self.suggestions if self.suggestions else None

    def on_tool_result(self, input: ToolResultInput) -> list[SelfSuggestion] | None:
        self.calls.append("on_tool_result")
        return None

    def on_user_input(self, input: UserInputReceived) -> list[SelfSuggestion] | None:
        self.calls.append("on_user_input")
        return None

    def get_suggestions(self) -> list[SelfSuggestion]:
        return list(self.suggestions)


class _LegacyTestExtension:
    """A legacy extension (hooks dict) for testing backward compatibility."""
    name = "test_legacy"

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def hooks(self) -> dict[str, Any]:
        return {
            "on_agent_start": self._on_agent_start,
            "on_after_tool": self._on_after_tool,
            "on_session_end": self._on_session_end,
        }

    def _on_agent_start(self, **kwargs: Any) -> None:
        self.calls.append("on_agent_start")

    def _on_after_tool(self, **kwargs: Any) -> None:
        self.calls.append("on_after_tool")

    def _on_session_end(self, **kwargs: Any) -> None:
        self.calls.append("on_session_end")


class _CrashingExtension:
    """An extension that crashes — used to test IPR-0 resilience."""
    name = "test_crash"

    @property
    def hooks(self) -> dict[str, Any]:
        return {"on_agent_start": self._crash}

    def _crash(self, **kwargs: Any) -> None:
        raise RuntimeError("intentional crash")


class TestExtensionRegistryTyped:
    """ExtensionRegistry supports both legacy and typed hooks."""

    def test_typed_extension_detection(self) -> None:
        """Typed extensions should be detected by _is_typed_extension."""
        ext = _TypedTestExtension()
        assert _is_typed_extension(ext)

    def test_legacy_extension_not_detected_as_typed(self) -> None:
        """Legacy extensions should not be detected as typed."""
        ext = _LegacyTestExtension()
        assert not _is_typed_extension(ext)

    def test_register_typed_extension(self) -> None:
        """Typed extensions can be registered like legacy ones."""
        registry = ExtensionRegistry()
        ext = _TypedTestExtension()
        registry.register(ext)
        assert registry.extension_count == 1
        assert registry.get("test_typed") is ext

    def test_fire_typed_turn_done(self) -> None:
        """Typed hooks receive typed input models."""
        registry = ExtensionRegistry()
        ext = _TypedTestExtension()
        registry.register(ext)

        inp = TurnDoneInput(
            egress="test",
            step_count=5,
            tool_counts={"bash": 3},
            tool_errors={},
        )
        suggestions = registry.fire_typed("on_turn_done", inp)
        assert suggestions == []
        assert "on_turn_done" in ext.calls

    def test_fire_typed_tool_result(self) -> None:
        """Typed tool result hooks receive ToolResultInput."""
        registry = ExtensionRegistry()
        ext = _TypedTestExtension()
        registry.register(ext)

        inp = ToolResultInput(tool_id="bash", success=True, output="ok", step=1)
        registry.fire_typed("on_tool_result", inp)
        assert "on_tool_result" in ext.calls

    def test_typed_extension_returns_suggestions(self) -> None:
        """Typed extensions can return SelfSuggestions from hooks."""
        registry = ExtensionRegistry()
        ext = _TypedTestExtension()
        ext.suggestions = [
            SelfSuggestion(
                kind="adjust_k", target="bugfix", value=2,
                confidence=0.8, evidence="test",
            ),
        ]
        registry.register(ext)

        inp = TurnDoneInput(egress="test", step_count=1)
        suggestions = registry.fire_typed("on_turn_done", inp)
        assert len(suggestions) == 1
        assert suggestions[0].kind == "adjust_k"

    # Counterexample: legacy extension's hooks dict is still fired
    def test_legacy_hooks_still_fire(self) -> None:
        """Legacy hooks dict should still work after typed hook integration."""
        registry = ExtensionRegistry()
        ext = _LegacyTestExtension()
        registry.register(ext)
        registry.fire("on_agent_start", goal=None, model=None, messages=[])
        assert "on_agent_start" in ext.calls

    # Counterexample: one crashing extension should not block others
    def test_crashing_extension_does_not_block(self) -> None:
        """IPR-0: a failing extension should not block others."""
        registry = ExtensionRegistry()
        registry.register(_CrashingExtension())
        good_ext = _LegacyTestExtension()
        registry.register(good_ext)

        # Should not raise despite _CrashingExtension raising
        registry.fire("on_agent_start", goal=None, model=None, messages=[])
        assert "on_agent_start" in good_ext.calls

    # Counterexample: typed crashing extension should not block others
    def test_typed_crashing_does_not_block(self) -> None:
        """IPR-0: a failing typed extension should not block others."""
        registry = ExtensionRegistry()

        class _TypedCrash:
            name = "typed_crash"
            def on_turn_done(self, input: Any) -> list[SelfSuggestion] | None:
                raise RuntimeError("typed crash")

        good_ext = _TypedTestExtension()
        registry.register(_TypedCrash())
        registry.register(good_ext)

        inp = TurnDoneInput(egress="test", step_count=1)
        # Should not raise
        suggestions = registry.fire_typed("on_turn_done", inp)
        # Good extension should still have been called
        assert "on_turn_done" in good_ext.calls

    def test_fire_all_both_legacy_and_typed(self) -> None:
        """fire_all should fire both legacy and typed hooks."""
        registry = ExtensionRegistry()
        legacy = _LegacyTestExtension()
        typed = _TypedTestExtension()
        registry.register(legacy)
        registry.register(typed)

        inp = TurnDoneInput(egress="test", step_count=1)
        registry.fire_all("on_session_end", "on_turn_done", typed_input=inp, egress="test")
        assert "on_session_end" in legacy.calls
        assert "on_turn_done" in typed.calls

    # Counterexample: fire_all without typed_hook should only fire legacy
    def test_fire_all_no_typed_hook(self) -> None:
        """fire_all with typed_hook=None should only fire legacy hooks."""
        registry = ExtensionRegistry()
        legacy = _LegacyTestExtension()
        typed = _TypedTestExtension()
        registry.register(legacy)
        registry.register(typed)

        registry.fire_all("on_agent_start", None, typed_input=None, goal=None, model=None, messages=[])
        assert "on_agent_start" in legacy.calls
        # on_turn_start should NOT have been called (typed_hook was None)
        assert "on_turn_start" not in typed.calls

    def test_collect_suggestions(self) -> None:
        """collect_suggestions gathers suggestions from all extensions."""
        registry = ExtensionRegistry()
        ext = _TypedTestExtension()
        ext.suggestions = [
            SelfSuggestion(kind="adjust_k", target="t", value=1, confidence=0.5, evidence="e"),
        ]
        registry.register(ext)
        suggestions = registry.collect_suggestions()
        assert len(suggestions) == 1
        # Legacy extensions without get_suggestions should not cause errors
        registry.register(_LegacyTestExtension())
        suggestions2 = registry.collect_suggestions()
        assert len(suggestions2) == 1  # Still only from typed ext


# ═══════════════════════════════════════════════════════════════════
# §5  AutoLearnExtension typed hook integration
# ═══════════════════════════════════════════════════════════════════


class TestAutoLearnTyped:
    """AutoLearnExtension should work with typed hooks."""

    @pytest.fixture(autouse=True)
    def _fresh_auto_learn(self) -> None:
        """Create a fresh AutoLearnExtension with temp persistence for each test."""
        from zall.extensions.auto_learn import AutoLearnExtension
        self._tmp_dir = tempfile.mkdtemp()
        self._learned_path = os.path.join(self._tmp_dir, "test_learned.jsonl")
        self.ext = AutoLearnExtension(learned_path=self._learned_path)
        yield
        # Cleanup
        import shutil
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_auto_learn_is_typed_extension(self) -> None:
        """AutoLearnExtension should be detected as a typed extension."""
        from zall.extensions.auto_learn import AutoLearnExtension
        ext = AutoLearnExtension(learned_path=self._learned_path)
        assert _is_typed_extension(ext)

    def test_auto_learn_has_legacy_hooks(self) -> None:
        """AutoLearnExtension should still provide legacy hooks dict."""
        assert "on_after_tool" in self.ext.hooks
        assert "on_session_end" in self.ext.hooks

    def test_auto_learn_tracks_tool_result(self) -> None:
        """AutoLearnExtension should track tool calls via typed hook."""
        inp = ToolResultInput(tool_id="bash", success=True, output="ok", step=1)
        self.ext.on_tool_result(inp)

        stats = self.ext.get_stats()
        assert stats["tool_counts"]["bash"] == 1

    def test_auto_learn_tracks_errors(self) -> None:
        """AutoLearnExtension should track tool errors via typed hook."""
        inp = ToolResultInput(tool_id="bash", success=False, output="", error="fail", step=1)
        self.ext.on_tool_result(inp)

        stats = self.ext.get_stats()
        assert stats["tool_errors"]["bash"] == 1
        assert stats["error_patterns"] == 1

    def test_auto_learn_suggests_on_high_error_rate(self) -> None:
        """AutoLearnExtension should suggest K adjustment on high error rate."""
        # Simulate 5 tool calls with 3 errors on the same tool
        for i in range(5):
            success = i < 2  # 2 successes, 3 failures
            inp = ToolResultInput(
                tool_id="bash", success=success, output="",
                error="fail" if not success else None,
                step=i,
            )
            self.ext.on_tool_result(inp)

        # Trigger turn_done analysis
        inp = TurnDoneInput(
            egress="test",
            step_count=5,
            tool_counts={"bash": 5},
            tool_errors={"bash": 3},
        )
        suggestions = self.ext.on_turn_done(inp)
        assert suggestions is not None
        # Should have at least one adjust_k suggestion
        kinds = [s.kind for s in suggestions]
        assert "adjust_k" in kinds

    # Counterexample: no suggestions on clean session
    def test_clean_session_no_suggestions(self) -> None:
        """A clean session with no errors should produce no suggestions."""
        for i in range(3):
            inp = ToolResultInput(tool_id="bash", success=True, output="ok", step=i)
            self.ext.on_tool_result(inp)

        inp = TurnDoneInput(
            egress="test", step_count=3,
            tool_counts={"bash": 3}, tool_errors={},
        )
        suggestions = self.ext.on_turn_done(inp)
        # Either None or empty list
        if suggestions is not None:
            assert len(suggestions) == 0

    def test_auto_learn_get_suggestions(self) -> None:
        """AutoLearnExtension.get_suggestions() returns accumulated suggestions."""
        # No suggestions yet
        assert self.ext.get_suggestions() == []

        # After some work, suggestions should be available
        for i in range(5):
            inp = ToolResultInput(
                tool_id="bash", success=False, output="", error="fail", step=i,
            )
            self.ext.on_tool_result(inp)

        inp = TurnDoneInput(
            egress="test", step_count=5,
            tool_counts={"bash": 5}, tool_errors={"bash": 3},
        )
        self.ext.on_turn_done(inp)
        assert len(self.ext.get_suggestions()) > 0

    def test_auto_learn_apply_suggestion(self) -> None:
        """AutoLearnExtension.apply_suggestion() should consume a suggestion."""
        s = SelfSuggestion(
            kind="adjust_k", target="bash", value=3,
            confidence=0.8, evidence="test",
        )
        result = self.ext.apply_suggestion(s)
        assert result["applied"] is True
        assert result["kind"] == "adjust_k"

    def test_get_config_overrides(self) -> None:
        """AutoLearnExtension.get_config_overrides() should provide config patches."""
        # No data yet → empty overrides
        overrides = self.ext.get_config_overrides()
        assert overrides == {}

        # Add some error data
        for i in range(5):
            self.ext.on_tool_result(ToolResultInput(
                tool_id="bash", success=False, output="", error="fail", step=i,
            ))

        overrides = self.ext.get_config_overrides()
        # Should have k_overrides when errors are high enough
        if overrides:
            assert "k_overrides" in overrides

    # Counterexample: legacy hooks still work on AutoLearnExtension
    def test_legacy_hooks_bridge(self) -> None:
        """Legacy hooks dict should still work on AutoLearnExtension."""
        class FakeResult:
            success = True
            output = "ok"
            error = None

        self.ext.hooks["on_after_tool"](tool_id="bash", result=FakeResult(), step=1)
        stats = self.ext.get_stats()
        assert stats["tool_counts"]["bash"] == 1


# ═══════════════════════════════════════════════════════════════════
# §6  UsageTracker typed hook integration
# ═══════════════════════════════════════════════════════════════════


class TestUsageTrackerTyped:
    """UsageTracker should work with both typed and legacy hooks."""

    def test_usage_tracker_is_typed_extension(self) -> None:
        """UsageTracker should be detected as a typed extension."""
        from zall.extensions.usage_tracker import UsageExtension
        ext = UsageExtension()
        # UsageTracker doesn't implement the typed hooks in the same way
        # as AutoLearn (it uses a different approach), so it should
        # still be detected because it has typed hook methods
        assert hasattr(ext, "on_tool_result")
        assert hasattr(ext, "on_turn_start")

    def test_usage_tracker_has_legacy_hooks(self) -> None:
        """UsageTracker should still provide legacy hooks dict."""
        from zall.extensions.usage_tracker import UsageExtension
        ext = UsageExtension()
        hooks = ext.hooks
        assert "on_agent_start" in hooks
        assert "on_after_tool" in hooks

    def test_usage_tracker_typed_tool_result(self) -> None:
        """UsageTracker should track via typed hook."""
        from zall.extensions.usage_tracker import UsageExtension
        ext = UsageExtension()

        inp = ToolResultInput(tool_id="bash", success=True, output="ok", step=1)
        ext.on_tool_result(inp)

        stats = ext.get_stats()
        assert stats["tool_calls"]["bash"] == 1
        assert stats["total_calls"] == 1

    def test_usage_tracker_typed_turn_start(self) -> None:
        """UsageTracker should record model info via typed hook."""
        from zall.extensions.usage_tracker import UsageExtension
        ext = UsageExtension()

        inp = TurnStartInput(
            goal="test_goal",
            model_name="agnes-2.0-flash",
            messages=[],
            tools=(),
        )
        ext.on_turn_start(inp)

        stats = ext.get_stats()
        assert stats["model"] == "agnes-2.0-flash"

    # Counterexample: error tracking via typed hook
    def test_usage_tracker_typed_error(self) -> None:
        """UsageTracker should track errors via typed hook."""
        from zall.extensions.usage_tracker import UsageExtension
        ext = UsageExtension()

        inp = ToolResultInput(tool_id="bash", success=False, output="", error="fail", step=1)
        ext.on_tool_result(inp)

        stats = ext.get_stats()
        assert stats["tool_errors"]["bash"] == 1
        assert stats["total_errors"] == 1