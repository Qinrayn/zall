"""zall.core.plan_mode — PlanModeTracker state machine.

Inspired by Grok Build's PlanModeTracker (xai-grok-shell/src/session/plan_mode.rs).

Plan mode is a read-only posture where the agent can only read/search/explore
but cannot write to files. The only writable file is plan.md.

State machine:
    Inactive → (user /plan or --plan) → Active → (user exit or /exit) → Inactive

Within Active state, write tools are blocked (return error) unless writing to plan.md.
Read tools execute normally.

The state is serializable (PlanModeSnapshot) so plan mode survives process restarts.

Usage:
    tracker = PlanModeTracker()
    tracker.activate()
    assert tracker.is_active
    assert not tracker.can_write("write_file")
    assert tracker.can_write("plan.md")  # plan.md is always writable
    tracker.deactivate()
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class PlanModeState(str, Enum):
    """Plan mode state machine states.

    Inactive → Active → Inactive
    """
    Inactive = "inactive"
    Active = "active"


class PlanModeTracker:
    """Plan mode state machine with serialization support.

    Manages the read-only posture for plan mode.
    In plan mode, write tools are blocked except for plan.md writes.
    """

    def __init__(self, initial_state: PlanModeState = PlanModeState.Inactive) -> None:
        self._state = initial_state
        # Plan mode metadata
        self._plan_file: str = "plan.md"

    # ── Properties ──

    @property
    def state(self) -> PlanModeState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state == PlanModeState.Active

    @property
    def is_inactive(self) -> bool:
        return self._state == PlanModeState.Inactive

    # ── State transitions ──

    def activate(self) -> None:
        """Enter plan mode (Inactive → Active).

        Raises:
            RuntimeError: If already active.
        """
        if self._state == PlanModeState.Active:
            raise RuntimeError("PlanModeTracker is already active")
        self._state = PlanModeState.Active

    def deactivate(self) -> None:
        """Exit plan mode (Active → Inactive).

        Raises:
            RuntimeError: If already inactive.
        """
        if self._state == PlanModeState.Inactive:
            raise RuntimeError("PlanModeTracker is already inactive")
        self._state = PlanModeState.Inactive

    # ── Permission checks ──

    def can_write(self, tool_id: str, args: dict[str, Any] | None = None) -> bool:
        """Check if a write operation is allowed in the current state.

        In plan mode, only writes to plan.md are allowed.
        Read operations are always allowed (checked by caller).

        Args:
            tool_id: The tool being called.
            args: Optional tool arguments (used to check if writing to plan.md).

        Returns:
            True if the write is allowed, False if blocked by plan mode.
        """
        if not self.is_active:
            return True  # Not in plan mode → all writes allowed

        # In plan mode, only plan.md writes are allowed
        if tool_id in ("write_file", "edit_file", "batch_edit"):
            if args and "file_path" in args:
                file_path = args["file_path"]
                if file_path.endswith("/plan.md") or file_path == "plan.md" or file_path.endswith("\\plan.md"):
                    return True
            return False

        # Bash is blocked in plan mode (unless clearly read-only)
        if tool_id == "bash":
            return False

        # Other write tools blocked
        return False

    def can_read(self, tool_id: str) -> bool:
        """Check if a read operation is allowed.

        In plan mode, read operations are always allowed.
        Outside plan mode, all operations are allowed.

        Args:
            tool_id: The tool being called.

        Returns:
            True if the read is allowed.
        """
        return True  # Read operations are always allowed

    # ── Serialization ──

    def snapshot(self) -> PlanModeSnapshot:
        """Create a serializable snapshot of the current state."""
        return PlanModeSnapshot(
            state=self._state.value,
            plan_file=self._plan_file,
        )

    @classmethod
    def from_snapshot(cls, snapshot: PlanModeSnapshot | None) -> PlanModeTracker:
        """Restore from a snapshot, or create a new tracker."""
        if snapshot is None:
            return cls()
        return cls(initial_state=PlanModeState(snapshot.state))

    def __repr__(self) -> str:
        return f"PlanModeTracker(state={self._state.value})"


class PlanModeSnapshot:
    """Serializable snapshot of PlanModeTracker state.

    Survives process restarts for plan mode persistence.
    """
    def __init__(self, state: str, plan_file: str = "plan.md") -> None:
        self.state = state
        self.plan_file = plan_file

    def __repr__(self) -> str:
        return f"PlanModeSnapshot(state={self.state})"