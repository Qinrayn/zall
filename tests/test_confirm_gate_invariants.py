"""confirm_gate invariant test (DESIGN.md §4.5).

IPR-0: each test must contain a counterexample.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zall.core.action import Action
from zall.core.gate import (
    ConfirmGate,
    EquivalenceRequest,
    GateResult,
    GateState,
    OverrideEvent,
    UserResponse,
    UserResponseType,
)
from zall.core.safety import Judgement, SafeLevel


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


def _make_action(tool_id: str = "bash", args: dict | None = None) -> Action:
    return Action(tool_id=tool_id, args=args or {"command": "echo hi"})


def _make_judgement(level: SafeLevel = SafeLevel.WHITELIST) -> Judgement:
    return Judgement(level=level)


# ──────────────────────────────────────────────────────────────────────────
# §4.5 whitelist branch
# ──────────────────────────────────────────────────────────────────────────


class TestWhitelistBranch:
    """whitelist → directly execute, 不等 user."""

    def test_whitelist_executes_immediately(self) -> None:
        """Happy path: whitelist judgement → EXECUTING, action_to_execute 有值."""
        action = _make_action()
        gate = ConfirmGate(action, _make_judgement(SafeLevel.WHITELIST))
        result = gate.process(response=None)
        assert result.state == GateState.EXECUTING
        assert result.action_to_execute == action

    def test_whitelist_does_not_await_user(self) -> None:
        """Counterexample: whitelist 不进 AWAITING_USER state.

        如果一个实现让 whitelist 也等 user 确认, agent 会被琐碎确认淹没.
        """
        action = _make_action()
        gate = ConfirmGate(action, _make_judgement(SafeLevel.WHITELIST))
        result = gate.process(response=None)
        assert result.state != GateState.AWAITING_USER


# ──────────────────────────────────────────────────────────────────────────
# §4.5 greylist branch
# ──────────────────────────────────────────────────────────────────────────


class TestGreylistBranch:
    """greylist → AWAITING_USER, 等 user response."""

    def test_greylist_awaits_user(self) -> None:
        """Happy path: greylist → AWAITING_USER."""
        gate = ConfirmGate(_make_action(), _make_judgement(SafeLevel.GREYLIST))
        result = gate.process(response=None)
        assert result.state == GateState.AWAITING_USER

    def test_greylist_accept_executes(self) -> None:
        """Happy path: greylist + user accept → EXECUTING."""
        gate = ConfirmGate(_make_action(), _make_judgement(SafeLevel.GREYLIST))
        gate.process(response=None)  # → AWAITING_USER
        result = gate.process(UserResponse(response_type=UserResponseType.ACCEPT))
        assert result.state == GateState.EXECUTING
        assert result.action_to_execute is not None

    def test_greylist_reject_rejects(self) -> None:
        """Happy path: greylist + user reject → REJECTED."""
        gate = ConfirmGate(_make_action(), _make_judgement(SafeLevel.GREYLIST))
        gate.process(response=None)
        result = gate.process(UserResponse(response_type=UserResponseType.REJECT))
        assert result.state == GateState.REJECTED
        assert result.rejection_reason is not None

    def test_greylist_timeout_suspends(self) -> None:
        """Happy path: greylist + timeout → SUSPENDED (不 REJECTED).

        §4.5: timeout → suspend, 不 reject, 挂起.
        Counterexample: 如果实现把 timeout 当 reject, agent 无法后续 resume.
        """
        gate = ConfirmGate(_make_action(), _make_judgement(SafeLevel.GREYLIST))
        gate.process(response=None)
        result = gate.process(UserResponse(response_type=UserResponseType.TIMEOUT))
        assert result.state == GateState.SUSPENDED
        assert result.state != GateState.REJECTED

    def test_suspended_can_resume(self) -> None:
        """Happy path: SUSPENDED + resume → AWAITING_USER (可resume)."""
        gate = ConfirmGate(_make_action(), _make_judgement(SafeLevel.GREYLIST))
        gate.process(response=None)
        gate.process(UserResponse(response_type=UserResponseType.TIMEOUT))  # → SUSPENDED
        result = gate.process(UserResponse(response_type=UserResponseType.RESUME))
        assert result.state == GateState.AWAITING_USER

    def test_greylist_modify_requires_modified_action(self) -> None:
        """Counterexample: MODIFY response 缺 modified_action → must raise.

        §4.5: modify → 修改参数后重新 gate.没有 modified_action 的 modify 无意义.
        """
        gate = ConfirmGate(_make_action(), _make_judgement(SafeLevel.GREYLIST))
        gate.process(response=None)
        with pytest.raises(RuntimeError, match="MODIFY response requires modified_action"):
            gate.process(UserResponse(response_type=UserResponseType.MODIFY))


# ──────────────────────────────────────────────────────────────────────────
# §4.5 blacklist branch
# ──────────────────────────────────────────────────────────────────────────


class TestBlacklistBranch:
    """blacklist → EQUIVALENCE_PROPOSED, 不execute原action."""

    def test_blacklist_proposes_equivalence(self) -> None:
        """Happy path: blacklist → EQUIVALENCE_PROPOSED + equivalence_request."""
        action = _make_action()
        gate = ConfirmGate(action, _make_judgement(SafeLevel.BLACKLIST))
        result = gate.process(response=None)
        assert result.state == GateState.EQUIVALENCE_PROPOSED
        assert result.equivalence_request is not None
        assert result.equivalence_request.original_action == action

    def test_blacklist_does_not_execute_original(self) -> None:
        """Counterexample: blacklist init dispatch 不产出 action_to_execute.

        §4.5: blacklist 不执行原动作.
        如果一个实现让 blacklist directly execute, agent 越界 → 严重 hijack.
        """
        gate = ConfirmGate(_make_action(), _make_judgement(SafeLevel.BLACKLIST))
        result = gate.process(response=None)
        assert result.action_to_execute is None

    def test_blacklist_override_produces_event(self) -> None:
        """Happy path: blacklist + user override → EXECUTING_WITH_OVERRIDE + override_event.

        §4.5 + §6.4: user 显式 override → 执行 + 触发审计.
        """
        action = _make_action()
        gate = ConfirmGate(action, _make_judgement(SafeLevel.BLACKLIST))
        gate.process(response=None)  # → EQUIVALENCE_PROPOSED
        result = gate.process(
            UserResponse(
                response_type=UserResponseType.OVERRIDE,
                override_text="user explicitly accepts risk",
            )
        )
        assert result.state == GateState.EXECUTING_WITH_OVERRIDE
        assert result.override_event is not None
        assert result.override_event.override_text == "user explicitly accepts risk"

    def test_blacklist_override_empty_text_raises(self) -> None:
        """Counterexample: OVERRIDE 缺 override_text → must raise.

        §6.4: override 须有理由文本 (审计要求).空 override = 偷偷越界.
        """
        gate = ConfirmGate(_make_action(), _make_judgement(SafeLevel.BLACKLIST))
        gate.process(response=None)
        with pytest.raises(RuntimeError, match="OVERRIDE response requires non-empty"):
            gate.process(
                UserResponse(response_type=UserResponseType.OVERRIDE, override_text=None)
            )

    def test_blacklist_reject_rejects(self) -> None:
        """Happy path: blacklist + user reject → REJECTED."""
        gate = ConfirmGate(_make_action(), _make_judgement(SafeLevel.BLACKLIST))
        gate.process(response=None)
        result = gate.process(UserResponse(response_type=UserResponseType.REJECT))
        assert result.state == GateState.REJECTED


# ──────────────────────────────────────────────────────────────────────────
# §4.5 state machineinvariant
# ──────────────────────────────────────────────────────────────────────────


class TestStateMachineInvariants:
    """confirm_gate state machineinvariant."""

    def test_terminal_state_cannot_process(self) -> None:
        """Counterexample: TERMINAL state再 process → must raise.

        防止已终结的 gate 被误用.
        """
        gate = ConfirmGate(_make_action(), _make_judgement(SafeLevel.WHITELIST))
        gate.process(response=None)  # → EXECUTING
        # mock进入 TERMINAL (实际由 Loop 标记)
        gate._state = GateState.TERMINAL  # type: ignore[private]
        with pytest.raises(RuntimeError, match="TERMINAL state"):
            gate.process(response=None)

    def test_pending_without_response_works(self) -> None:
        """Happy path: PENDING + 无 response → init dispatch."""
        gate = ConfirmGate(_make_action(), _make_judgement(SafeLevel.WHITELIST))
        result = gate.process(response=None)
        assert result.state == GateState.EXECUTING

    def test_non_pending_without_response_raises(self) -> None:
        """Counterexample: non- PENDING + 无 response → must raise.

        AWAITING_USER 状态必须有 response 才能 process.
        """
        gate = ConfirmGate(_make_action(), _make_judgement(SafeLevel.GREYLIST))
        gate.process(response=None)  # → AWAITING_USER
        with pytest.raises(RuntimeError, match="without response"):
            gate.process(response=None)


# ──────────────────────────────────────────────────────────────────────────
# §6.4 OverrideEvent invariant
# ──────────────────────────────────────────────────────────────────────────


class TestOverrideEventInvariants:
    """OverrideEvent invariant (§6.4 audit)."""

    def test_frozen_immutable(self) -> None:
        """Counterexample: OverrideEvent construct后改 override_text → must raise."""
        event = OverrideEvent(
            original_action=_make_action(),
            original_judgement=_make_judgement(SafeLevel.BLACKLIST),
            override_text="reason",
        )
        with pytest.raises(ValidationError):
            event.override_text = "tampered"  # type: ignore[misc]

    def test_no_tool_history_marker(self) -> None:
        """OverrideEvent 不携带 tool 历史 (§4.3 核心斩断呼应)."""
        assert OverrideEvent.__no_tool_history__() is True


# ──────────────────────────────────────────────────────────────────────────
# §4.5 EquivalenceRequest invariant
# ──────────────────────────────────────────────────────────────────────────


class TestEquivalenceRequestInvariants:
    """EquivalenceRequest invariant."""

    def test_frozen_immutable(self) -> None:
        """Counterexample: EquivalenceRequest construct后改 → must raise."""
        req = EquivalenceRequest(
            original_action=_make_action(),
            original_judgement=_make_judgement(SafeLevel.BLACKLIST),
        )
        with pytest.raises(ValidationError):
            req.original_action = _make_action("read_file")  # type: ignore[misc]
