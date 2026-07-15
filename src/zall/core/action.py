"""zall.core.action — Action primitive (DESIGN.md §4.2 input side).

Corresponds to:
  §4.2  AuthorityLayer where context_judge receives its first input argument
  §4.2.1 context_judge(action, context) -> SafeLevel

Action describes what the agent intends to do. Its minimal fields:
  - tool_id: which tool (e.g. "bash", "read_file")
  - args:    tool arguments (e.g. {"command": "git push"})

DESIGN.md does not explicitly define the field structure of Action
(implementation-level detail, shape deferred).
This file provides the minimal shape, extensible in future iterations.

IPR constraints:
  IPR-0: invariant tests at tests/test_action_invariants.py, includesCounterexample
  IPR-1: this file corresponds to DESIGN.md §4.2
  IPR-3: pydantic / stdlib only, no model SDK
  IPR-4: this file is a primitive, no main Loop
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class Action(BaseModel):
    """Description of what the agent intends to do (DESIGN.md §4.2 input side).

    IPR-0 invariants:
        - frozen (tool_id cannot be reassigned, keeping context_judge results reproducible)
        - tool_id must be non-empty (validator enforces this)

    Known OPEN:
        - args: dict is mutable — pydantic frozen does not prevent dict internal mutation.
          Same pattern as v0.0.5 commitment boundary: does not pretend to do what it cannot.
          args immutability is OPEN; invariant tests only test tool_id frozen, not args.
          Future tightening via MappingProxyType / deep copy (deferred).
    """

    model_config = ConfigDict(frozen=True)

    tool_id: str
    args: dict[str, Any] = {}

    @field_validator("tool_id")
    @classmethod
    def _tool_id_must_be_non_empty(cls, v: str) -> str:
        """tool_id must be non-empty (empty tool_id is meaningless).

        Counterexample: tool_id="" must raise (context_judge cannot match rules).
        """
        if not v:
            raise ValueError("tool_id must be non-empty")
        return v

    @staticmethod
    def __no_tool_history__() -> bool:
        """Declares: Action does not carry tool invocation history (§4.3 core severance).

        Action describes "what to do", not "what was done". History is explicitly
        excluded from Context.
        """
        return True