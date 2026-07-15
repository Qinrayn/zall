"""Action invariant tests (DESIGN.md §4.2 input side).

IPR-0: each test must contain a counterexample.
Counterexample summary in tests/INVARIANTS.md.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zall.core.action import Action


class TestActionInvariants:
    """§4.2 Action invariants."""

    def test_happy_path_constructs(self) -> None:
        """Happy path: valid Action can be constructed."""
        action = Action(tool_id="bash", args={"command": "git push"})
        assert action.tool_id == "bash"
        assert action.args["command"] == "git push"

    def test_empty_tool_id_raises(self) -> None:
        """Counterexample: empty tool_id must raise (empty tool_id is meaningless).

        If an implementation lets an empty tool_id pass, context_judge cannot match rules.
        """
        with pytest.raises(ValidationError):
            Action(tool_id="", args={})

    def test_frozen_tool_id_immutable(self) -> None:
        """Counterexample: post-construction mutation of tool_id must raise (frozen).

        Prevents non-reproducible context_judge results: same Action's tool_id cannot change.
        """
        action = Action(tool_id="bash", args={})
        with pytest.raises(ValidationError):
            action.tool_id = "read_file"  # type: ignore[misc]

    def test_no_tool_history_marker(self) -> None:
        """Action does not carry tool history (§4.3 core severance counterpart).

        Action is "what to do", not "what was done".
        """
        assert Action.__no_tool_history__() is True

    def test_args_dict_known_open_mutability(self) -> None:
        """Known OPEN: args: dict is mutable (pydantic frozen does not prevent internal mutation).

        This test is **not a counterexample**, but an honest record: args immutability is OPEN,
        same pattern as v0.0.5 "promise boundary" — don't pretend to do what can't be done.
        When tightened to MappingProxyType / deep copy, this test should become a counterexample.
        """
        action = Action(tool_id="bash", args={"command": "git push"})
        # args is a dict, mutable (known OPEN, not pretending immutability)
        assert isinstance(action.args, dict)
