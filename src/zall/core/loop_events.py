"""zall.core.loop_events — LoopEvent, RunEgress, StepResult + constants."""

from __future__ import annotations
from typing import Any
from pydantic import BaseModel, ConfigDict
from zall.core.goal import GoalTriple, TerminationState

MAX_STEPS = 50


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
    # §12.1 Verifiability: 运行时链完整性自检结果 (None=链完整, str=篡改警告)
    # MASTER.md §12.1 Verifiability + §3.1.4 运行时自检
    chain_warning: str | None = None

class StepResult(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    kind: str
    egress: RunEgress | None = None
    content: str = ""
    tools_used: tuple[str, ...] = ()

    @property
    def is_terminal(self) -> bool:
        return self.kind == "terminal"
