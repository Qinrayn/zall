"""UsageTracker extension — Tracks tool usage and model call statistics.

Hooks:
  on_after_tool — Increments tool call counters
  on_agent_start — Records model and goal info

Provides a /stats command for displaying usage data.
"""

from __future__ import annotations

from typing import Any
from collections import defaultdict


class UsageExtension:
    """Tracks usage statistics across a session.

    Data available via get_stats() for the /stats command.
    """

    name = "usage_tracker"

    def __init__(self) -> None:
        self._tool_counts: dict[str, int] = defaultdict(int)
        self._tool_errors: dict[str, int] = defaultdict(int)
        self._model_name: str = ""
        self._goal_type: str = ""
        self._step_count: int = 0

    @property
    def hooks(self) -> dict[str, Any]:
        return {
            "on_agent_start": self._on_agent_start,
            "on_after_tool": self._on_after_tool,
        }

    def _on_agent_start(self, goal: Any = None, model: Any = None, **kwargs: Any) -> None:
        self._model_name = getattr(model, "model_name", "") if model else ""
        self._goal_type = (
            goal.statement.goal_type.value
            if goal and hasattr(goal, "statement") and hasattr(goal.statement, "goal_type")
            else ""
        )

    def _on_after_tool(self, tool_id: str, result: Any, step: int = 0, **kwargs: Any) -> None:
        self._tool_counts[tool_id] += 1
        self._step_count = max(self._step_count, step)
        if hasattr(result, "success") and not result.success:
            self._tool_errors[tool_id] += 1

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