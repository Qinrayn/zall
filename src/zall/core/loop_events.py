"""zall.core.loop_events — LoopEvent, RunEgress, StepResult + constants."""

from __future__ import annotations
from typing import Any
from pydantic import BaseModel, ConfigDict
from zall.core.goal import GoalTriple, TerminationState

MAX_STEPS = 50

_EMPTY_STOP_NUDGE = (
    "Your previous turn produced no tool_call and no useful answer. You MUST now emit a "
    "tool_call to actually perform the user''s request (bash / write_file / edit_file / "
    "list_dir / grep / etc.). Do NOT reply with text that only describes what you intend "
    "to do (eg. ''I will create ...'') — that is a failure. Execute the action via a "
    "tool_call in THIS turn. If the request is truly a pure question that needs no tool, "
    "answer it concisely and substantively. Never return an empty response."
)

class LoopEvent(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    kind: str
    step: int
    payload: dict[str, Any] = {}

class RunEgress(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    run_id: str
    final_state: TerminationState
    step_count: int
    total_tool_calls: int
    total_model_calls: int
    error: str | None = None
    original_goal: GoalTriple | None = None
    candidate_goals: tuple[GoalTriple, ...] = ()
    downgrade_depth: int = 0
    final_claim: str = ""

class StepResult(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    kind: str
    egress: RunEgress | None = None
    content: str = ""
    tools_used: tuple[str, ...] = ()

    @property
    def is_terminal(self) -> bool:
        return self.kind == "terminal"
