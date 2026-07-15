"""zall.core.gate — confirm_gate state machine (DESIGN.md §4.5).

Corresponds to:
  §4.5   confirm_gate: gate(action, context) -> execute / suspend / override
         whitelist → execute
         greylist  → on_user_response: accept/reject/modify/timeout→suspend
         blacklist → 不执行原动作; 提供等价替换; user Override → 执行 + 审计
  §6.4   Override 审计 (confirm_gate 产出 OverrideEvent, RunRecorder 消费)

IPR constraints:
  IPR-0: invariant tests at tests/test_confirm_gate_invariants.py, includesCounterexample
  IPR-1: this file corresponds to DESIGN.md §4.5
  IPR-3: pydantic / stdlib only, no model SDK
  IPR-4: this file is a primitive, no main Loop
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from zall.core.action import Action
from zall.core.safety import Judgement, SafeLevel


# ──────────────────────────────────────────────────────────────────────────
# §4.5 GateState (state machine)
# ──────────────────────────────────────────────────────────────────────────


class GateState(str, Enum):
    """confirm_gate 的state (DESIGN.md §4.5)。

    状态转移:
        deferred    → (context_judge 结果) → EXECUTING / AWAITING_USER / EQUIVALENCE_PROPOSED
        AWAITING_USER → (user response) → EXECUTING / REJECTED / SUSPENDED / (modify→REJUDGE)
        REJUDGE     → (loop 重跑 context_judge) → EXECUTING / AWAITING_USER / EQUIVALENCE_PROPOSED
        SUSPENDED  → (resume) → AWAITING_USER
        EQUIVALENCE_PROPOSED → (user accept equiv) → EXECUTING / (user override) → EXECUTING_WITH_OVERRIDE
        EXECUTING / REJECTED / EXECUTING_WITH_OVERRIDE → TERMINAL
    """

    deferred = "deferred"
    EXECUTING = "executing"
    AWAITING_USER = "awaiting_user"
    EQUIVALENCE_PROPOSED = "equivalence_proposed"
    SUSPENDED = "suspended"
    REJUDGE = "rejudge"  # B5: MODIFY 后需要 Loop 重跑 context_judge
    REJECTED = "rejected"
    EXECUTING_WITH_OVERRIDE = "executing_with_override"
    TERMINAL = "terminal"


# ──────────────────────────────────────────────────────────────────────────
# §4.5 UserResponse (传输机制由应用层inject)
# ──────────────────────────────────────────────────────────────────────────


class UserResponseType(str, Enum):
    """user 对 greylist/blacklist action 的responsetype。"""

    ACCEPT = "accept"          # 接受执行
    REJECT = "reject"          # 拒绝
    MODIFY = "modify"          # 修改参数后重新 gate
    ACCEPT_EQUIVALENCE = "accept_equivalence"  # 接受等价替换
    OVERRIDE = "override"      # 显式 override blacklist (触发审计)
    RESUME = "resume"          # 从 suspended 恢复
    TIMEOUT = "timeout"        # 等待超时

    # §3.4.4 Goal Downgrade gate: downgrade专用的用户response
    ACCEPT_DOWNGRADE = "accept_downgrade"    # 接受降级候选 (选哪个由 downgrade_index 指定)
    REJECT_DOWNGRADE = "reject_downgrade"    # 拒绝降级, 走 Decline


class UserResponse(BaseModel):
    """user response数据结构。"""

    model_config = ConfigDict(frozen=True)

    response_type: UserResponseType
    modified_action: Action | None = None  # only MODIFY 时有值
    override_text: str | None = None  # only OVERRIDE 时有值 (须非空)
    downgrade_index: int = 0  # §3.4.4: only ACCEPT_DOWNGRADE 时有值 (选中第几个 candidate)


@runtime_checkable
class UserResponder(Protocol):
    """user response的传输机制 (应用层inject, core 不绑死)。

    纯接口: 返回 UserResponse。具体实现可以是 CLI 输入 / API callback / WebSocket。
    """

    def ask(self, action: Action, judgement: Judgement) -> UserResponse: ...


# ──────────────────────────────────────────────────────────────────────────
# §4.5 / §6.4 event产出 (不耦合消费方)
# ──────────────────────────────────────────────────────────────────────────


class OverrideEvent(BaseModel):
    """blacklist + user Override event (DESIGN.md §4.5 + §6.4)。

    confirm_gate 产出此结构, RunRecorder (未落码) 消费它做审计。
    confirm_gate 不直接调 RunRecorder —— 事件产出, 不耦合消费方。

    IPR-0 不变量:
        - frozen
        - override_text 非空 (user 须显式说明 override 理由)
    """

    model_config = ConfigDict(frozen=True)

    original_action: Action
    original_judgement: Judgement
    override_text: str

    @staticmethod
    def __no_tool_history__() -> bool:
        return True


class EquivalenceRequest(BaseModel):
    """blacklist + user 不 Override 时的等价replacerequest (DESIGN.md §4.5)。

    confirm_gate 产出此结构, equivalence 函数 (未落码) 消费它生成等价替换建议。
    confirm_gate 不实现 equivalence 函数 —— 事件产出, 不耦合消费方。
    """

    model_config = ConfigDict(frozen=True)

    original_action: Action
    original_judgement: Judgement


# ──────────────────────────────────────────────────────────────────────────
# §4.5 GateResult (gate 的产出)
# ──────────────────────────────────────────────────────────────────────────


class GateResult(BaseModel):
    """confirm_gate handle一轮后的产出。

    可能是:
      - 直接执行 (whitelist / user accept)
      - 等价替换执行 (user accept_equivalence)
      - 带 override 执行 (user override blacklist)
      - 拒绝 (user reject)
      - 挂起 (timeout)
      - 等价替换请求 (blacklist + user 不 override)
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    state: GateState
    action_to_execute: Action | None = None  # 若 state=EXECUTING / EXECUTING_WITH_OVERRIDE
    override_event: OverrideEvent | None = None  # 若 state=EXECUTING_WITH_OVERRIDE
    equivalence_request: EquivalenceRequest | None = None  # 若 state=EQUIVALENCE_PROPOSED
    rejection_reason: str | None = None  # 若 state=REJECTED


# ──────────────────────────────────────────────────────────────────────────
# §4.5 ConfirmGate state machine
# ──────────────────────────────────────────────────────────────────────────


class ConfirmGate:
    """confirm_gate state machine (DESIGN.md §4.5)。

    用法:
        gate = ConfirmGate(action, judgement)
        result = gate.process(response=None)  # 第一轮: 根据 judgement 决定初始状态
        # 若 result.state == AWAITING_USER:
        #   response = user_responder.ask(action, judgement)
        #   result = gate.process(response)
        # 若 result.state == SUSPENDED:
        #   response = UserResponse(response_type=UserResponseType.RESUME)
        #   result = gate.process(response)
    """

    def __init__(self, action: Action, judgement: Judgement) -> None:
        self._action = action
        self._judgement = judgement
        self._state: GateState = GateState.deferred
        self._modified_action: Action | None = None

    @property
    def state(self) -> GateState:
        return self._state

    @property
    def current_action(self) -> Action:
        """当前要execute的 action (可能是 modify 后的)。"""
        return self._modified_action or self._action

    def process(self, response: UserResponse | None = None) -> GateResult:
        """handle一轮 user response (或第一轮无 response)。

        纯状态转移: 不调外部服务, 不执行 action 本身。
        执行 action 是 Agent Loop 的职责 (IPR-4)。
        """
        if self._state == GateState.TERMINAL:
            raise RuntimeError("ConfirmGate already in TERMINAL state")

        # ── 第一轮: 根据 judgement 决定initstate
        if self._state == GateState.deferred and response is None:
            return self._initial_dispatch()

        # ── 后续轮: 根据 response 转移state
        if response is None:
            raise RuntimeError("process() called without response in non-deferred state")

        if self._state == GateState.AWAITING_USER:
            return self._handle_user_response(response)

        if self._state == GateState.SUSPENDED:
            return self._handle_resume(response)

        if self._state == GateState.EQUIVALENCE_PROPOSED:
            return self._handle_equivalence_response(response)

        if self._state == GateState.REJUDGE:
            # REJUDGE 是最终state: Loop 看到后重跑 context_judge, 不在此handle
            raise RuntimeError("REJUDGE state must be handled by Loop, not by gate.process()")

        raise RuntimeError(f"Unexpected state {self._state} with response {response}")

    def _initial_dispatch(self) -> GateResult:
        """第一轮: 根据 context_judge 的 judgement 决定initstate。"""
        level = self._judgement.level

        if level == SafeLevel.WHITELIST:
            self._state = GateState.EXECUTING
            return GateResult(
                state=GateState.EXECUTING,
                action_to_execute=self.current_action,
            )

        if level == SafeLevel.GREYLIST:
            self._state = GateState.AWAITING_USER
            return GateResult(state=GateState.AWAITING_USER)

        if level == SafeLevel.BLACKLIST:
            # blacklist 不execute原action; 产出等价replacerequest
            # user 可选: accept_equivalence / override / reject
            self._state = GateState.EQUIVALENCE_PROPOSED
            return GateResult(
                state=GateState.EQUIVALENCE_PROPOSED,
                equivalence_request=EquivalenceRequest(
                    original_action=self.current_action,
                    original_judgement=self._judgement,
                ),
            )

        # 不可能到达 (SafeLevel 只有three-state, 与 v0.0.7 4 态自驳一致)
        raise RuntimeError(f"Unexpected SafeLevel {level}")

    def _handle_user_response(self, response: UserResponse) -> GateResult:
        """greylist AWAITING_USER state下handle user response。"""
        rt = response.response_type

        if rt == UserResponseType.ACCEPT:
            self._state = GateState.EXECUTING
            return GateResult(
                state=GateState.EXECUTING,
                action_to_execute=self.current_action,
            )

        if rt == UserResponseType.REJECT:
            self._state = GateState.REJECTED
            return GateResult(
                state=GateState.REJECTED,
                rejection_reason="user rejected",
            )

        if rt == UserResponseType.MODIFY:
            if response.modified_action is None:
                raise RuntimeError("MODIFY response requires modified_action")
            # B5: modify → REJUDGE, 让 Loop 知道需要重跑 context_judge
            # 区别于 deferred (第一轮 dispatch), REJUDGE 明确表示"已修改，重判"
            self._modified_action = response.modified_action
            self._state = GateState.REJUDGE
            return GateResult(state=GateState.REJUDGE)

        if rt == UserResponseType.TIMEOUT:
            self._state = GateState.SUSPENDED
            return GateResult(state=GateState.SUSPENDED)

        # ACCEPT_EQUIVALENCE / OVERRIDE / RESUME 在 AWAITING_USER state下不Valid
        raise RuntimeError(f"Unexpected response {rt} in AWAITING_USER state")

    def _handle_resume(self, response: UserResponse) -> GateResult:
        """SUSPENDED state下handle resume。"""
        if response.response_type != UserResponseType.RESUME:
            raise RuntimeError(f"SUSPENDED state only accepts RESUME, got {response.response_type}")
        self._state = GateState.AWAITING_USER
        return GateResult(state=GateState.AWAITING_USER)

    def _handle_equivalence_response(self, response: UserResponse) -> GateResult:
        """EQUIVALENCE_PROPOSED state下handle user response。"""
        rt = response.response_type

        if rt == UserResponseType.ACCEPT_EQUIVALENCE:
            # equivalence function未implementation, 不得downgrade为execute原始 BLACKLIST action (security隐患)
            self._state = GateState.REJECTED
            return GateResult(
                state=GateState.REJECTED,
                action_to_execute=None,
                rejection_reason="equivalence replacement not yet implemented; action rejected for safety",
            )

        if rt == UserResponseType.OVERRIDE:
            # user 显式 override blacklist —— 触发audit
            if not response.override_text:
                raise RuntimeError("OVERRIDE response requires non-empty override_text")
            override_event = OverrideEvent(
                original_action=self.current_action,
                original_judgement=self._judgement,
                override_text=response.override_text,
            )
            self._state = GateState.EXECUTING_WITH_OVERRIDE
            return GateResult(
                state=GateState.EXECUTING_WITH_OVERRIDE,
                action_to_execute=self.current_action,
                override_event=override_event,
            )

        if rt == UserResponseType.REJECT:
            self._state = GateState.REJECTED
            return GateResult(
                state=GateState.REJECTED,
                rejection_reason="user rejected blacklist action",
            )

        raise RuntimeError(f"Unexpected response {rt} in EQUIVALENCE_PROPOSED state")
