"""zall.core.lifecycle — Typed lifecycle hooks (Pi-style self-evolving agent).

Inspired by Grok Build's xai-agent-lifecycle trait-based contributors.
Replaces the earlier loosely-typed Extension hook system with typed
input/output contracts that enable self-evolution extensions to produce
actionable SelfSuggestion results.

Two systems coexist:
  - Legacy hooks (Extension.hooks dict, kwargs-based) — backward compatible
  - Typed hooks (Protocol-based, typed input models) — the new standard

SelfSuggestion is the core self-evolution contract: extensions don't just
"observe" — they can "propose" configuration changes, new GoalTypes, or
skills based on observed patterns.

Corresponds to:
  §3.3.6  Refiner residual wounds → extensions can suggest refinements
  §3.6    Self-innovation capability (OPEN → SETTLED by this module)
  §4.4    K-value table adjustments via SelfSuggestion.adjust_k
  §5.2    Judge composition adjustments via SelfSuggestion
  §7      Long-term property: extensibility

IPR constraints:
  IPR-0: invariant tests at tests/test_lifecycle_hooks.py, includesCounterexample
  IPR-1: corresponds to DESIGN.md §3.3.6 + §3.6 + §4.4 + §5.2 + §7
  IPR-3: pydantic / stdlib only, no model SDK
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

# ── Forward references (avoid circular imports at module level) ──
# These are resolved at fire time via the typed inputs.


# ═══════════════════════════════════════════════════════════════════
# §1  Typed Input Models
# ═══════════════════════════════════════════════════════════════════


class TurnStartInput(BaseModel):
    """Input payload for on_turn_start hook.

    Carries the full goal, model identity, and initial message state
    so extensions can analyse the task before any work is done.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    goal: Any  # GoalTriple (avoid circular import)
    model_name: str
    messages: list
    tools: tuple  # ToolRegistry.tools
    step: int = 0

    @property
    def goal_type(self) -> str:
        """Convenience: extract goal type string."""
        if hasattr(self.goal, "statement") and hasattr(self.goal.statement, "goal_type"):
            return self.goal.statement.goal_type.value
        return "unknown"


class ToolResultInput(BaseModel):
    """Input payload for on_tool_result hook.

    Carries full tool execution detail so extensions can detect
    error patterns, tool chains, and usage frequency.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    tool_id: str
    success: bool
    output: str
    error: str | None = None
    step: int = 0
    duration: float = 0.0
    args: dict[str, Any] = {}


class TurnDoneInput(BaseModel):
    """Input payload for on_turn_done hook.

    Carries complete session summary so extensions can propose
    optimisations based on aggregate session data.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    egress: Any  # RunEgress (avoid circular import)
    step_count: int
    tool_counts: dict[str, int] = {}
    tool_errors: dict[str, int] = {}
    duration: float = 0.0
    goal_type: str = "unknown"

    @property
    def total_tool_calls(self) -> int:
        return sum(self.tool_counts.values())

    @property
    def error_rate(self) -> float:
        total = self.total_tool_calls
        if total == 0:
            return 0.0
        return sum(self.tool_errors.values()) / total


class UserInputReceived(BaseModel):
    """Input payload for on_user_input hook."""

    model_config = ConfigDict(frozen=True)

    content: str
    step: int = 0


# ═══════════════════════════════════════════════════════════════════
# §2  SelfSuggestion — Self-Evolution Contract
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SelfSuggestion:
    """A self-evolution proposal from an extension.

    The core contract of the self-evolving agent: extensions analyse
    runtime data and produce actionable suggestions. The agent or user
    can then apply these suggestions.

    Kind variants:
      adjust_k         — Change base_K for a GoalType (§4.4)
      register_goaltype — Register a new ExtendedGoalType (§3.5)
      add_rule          — Add a safety rule (§4.2)
      create_skill      — Create a reusable skill from a tool chain (§9.2.7)
      adjust_judge      — Change Judge composition for a GoalType (§5.2)
    """

    kind: Literal[
        "adjust_k",
        "register_goaltype",
        "add_rule",
        "create_skill",
        "adjust_judge",
    ]
    target: str  # What to adjust (e.g. "bugfix", "grok-build", "system_judge")
    value: Any  # The new value (e.g. 2 for K, or a GoalType name)
    confidence: float  # 0.0-1.0, how confident the extension is
    evidence: str  # Human-readable explanation of why

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {self.confidence}")


# ═══════════════════════════════════════════════════════════════════
# §3  Typed Hook Protocols
# ═══════════════════════════════════════════════════════════════════


@runtime_checkable
class TurnLifecycleHook(Protocol):
    """Protocol for hooks that observe turn lifecycle.

    Implement one or more methods. Each returns an optional list of
    SelfSuggestion — return None or [] if no suggestion.
    """

    def on_turn_start(self, input: TurnStartInput) -> list[SelfSuggestion] | None:
        """Called when a turn begins (goal + model + messages ready)."""
        return None

    def on_turn_done(self, input: TurnDoneInput) -> list[SelfSuggestion] | None:
        """Called when a turn ends (egress available)."""
        return None


@runtime_checkable
class ToolLifecycleHook(Protocol):
    """Protocol for hooks that observe tool execution."""

    def on_tool_result(self, input: ToolResultInput) -> list[SelfSuggestion] | None:
        """Called after each tool execution."""
        return None


@runtime_checkable
class UserInputHook(Protocol):
    """Protocol for hooks that observe user input."""

    def on_user_input(self, input: UserInputReceived) -> list[SelfSuggestion] | None:
        """Called when user input is received."""
        return None


# Composite protocol for extensions that implement all hooks
@runtime_checkable
class TypedExtension(Protocol):
    """A full typed extension implementing all lifecycle hooks.

    Extensions can implement TurnLifecycleHook, ToolLifecycleHook,
    and/or UserInputHook. This protocol combines them for convenience.
    """

    @property
    def name(self) -> str: ...

    def on_turn_start(self, input: TurnStartInput) -> list[SelfSuggestion] | None: ...

    def on_turn_done(self, input: TurnDoneInput) -> list[SelfSuggestion] | None: ...

    def on_tool_result(self, input: ToolResultInput) -> list[SelfSuggestion] | None: ...

    def on_user_input(self, input: UserInputReceived) -> list[SelfSuggestion] | None: ...


# ═══════════════════════════════════════════════════════════════════
# §4  Suggestion Accumulator
# ═══════════════════════════════════════════════════════════════════


class SuggestionAccumulator:
    """Collects SelfSuggestions from multiple typed hook calls.

    Used by ExtensionRegistry to aggregate suggestions across all
    registered extensions during a single hook fire.
    """

    def __init__(self) -> None:
        self._suggestions: list[SelfSuggestion] = []

    def add(self, suggestion: SelfSuggestion) -> None:
        self._suggestions.append(suggestion)

    def add_all(self, suggestions: list[SelfSuggestion]) -> None:
        self._suggestions.extend(suggestions)

    @property
    def suggestions(self) -> list[SelfSuggestion]:
        return list(self._suggestions)

    def clear(self) -> None:
        self._suggestions.clear()

    def by_kind(self, kind: str) -> list[SelfSuggestion]:
        return [s for s in self._suggestions if s.kind == kind]

    def __bool__(self) -> bool:
        return len(self._suggestions) > 0

    def __len__(self) -> int:
        return len(self._suggestions)

    def __repr__(self) -> str:
        if not self._suggestions:
            return "SuggestionAccumulator(empty)"
        return f"SuggestionAccumulator({len(self._suggestions)} suggestions)"