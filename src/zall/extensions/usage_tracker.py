"""UsageTracker extension — Tracks tool usage and model call statistics.

Hooks (typed):
  on_tool_result — Increments tool call counters
  on_turn_start — Records model and goal info

Legacy hooks (backward compatible):
  on_after_tool — Same as on_tool_result (bridged)
  on_agent_start — Same as on_turn_start (bridged)

Provides a /stats command for displaying usage data.
"""

from __future__ import annotations

from typing import Any
from collections import defaultdict

from zall.core.lifecycle import ToolResultInput, TurnStartInput


class UsageExtension:
    """Tracks usage statistics across a session.

    Data available via get_stats() for the /stats command.
    Supports both typed and legacy hook interfaces.
    """

    name = "usage_tracker"

    def __init__(self) -> None:
        self._tool_counts: dict[str, int] = defaultdict(int)
        self._tool_errors: dict[str, int] = defaultdict(int)
        self._model_name: str = ""
        self._goal_type: str = ""
        self._step_count: int = 0

    # ── Legacy hooks dict (backward compatible) ──

    @property
    def hooks(self) -> dict[str, Any]:
        return {
            "on_agent_start": self._on_agent_start_legacy,
            "on_after_tool": self._on_after_tool_legacy,
        }

    def _on_agent_start_legacy(self, goal: Any = None, model: Any = None, **kwargs: Any) -> None:
        self._model_name = getattr(model, "model_name", "") if model else ""
        self._goal_type = (
            goal.statement.goal_type.value
            if goal and hasattr(goal, "statement") and hasattr(goal.statement, "goal_type")
            else ""
        )

    def _on_after_tool_legacy(self, tool_id: str, result: Any, step: int = 0, **kwargs: Any) -> None:
        self._tool_counts[tool_id] += 1
        self._step_count = max(self._step_count, step)
        if hasattr(result, "success") and not result.success:
            self._tool_errors[tool_id] += 1

    # ── Typed hooks (new, v0.3.0+) ──

    def on_turn_start(self, input: TurnStartInput) -> list[Any] | None:
        """Record model and goal info from typed input."""
        self._model_name = input.model_name or ""
        self._goal_type = input.goal_type
        return None

    def on_tool_result(self, input: ToolResultInput) -> list[Any] | None:
        """Record tool execution from typed input."""
        self._tool_counts[input.tool_id] += 1
        self._step_count = max(self._step_count, input.step)
        if not input.success:
            self._tool_errors[input.tool_id] += 1
        return None

    def get_stats(self) -> dict[str, Any]:
        """Return usage statistics."""
        return {
            "model": self._model_name,
            "goal_type": self._goal_type,
            "steps": self._step_count,
            "tool_calls": dict(self._tool_counts),
            "tool_errors": dict(self._tool_errors),
            "total_calls": sum(self._tool_counts.values()),
            "total_errors": sum(self._tool_errors.values()),
        }


def create_usage_tracker() -> UsageExtension:
    return UsageExtension()