"""zall.core.executor — ToolExecutor: focused tool execution orchestrator.

Extracted from AgentLoop._execute_tool_calls() and _process_gate() to
reduce the ~1768-line loop.py into focused collaborators.

Responsibility:
  For each tool call: context_judge → confirm_gate → execute → checkpoint
  Handles gate state machine (SUSPENDED/REJUDGE/MODIFY/OVERRIDE)

Holds a back-reference to AgentLoop for accessing shared state (messages,
recorder, event bus, tool registry, etc.). This is intentional — ToolExecutor
is a private collaborator, not a public API.

Corresponds to:
  §4.2.1  context_judge safety evaluation
  §4.5    confirm_gate state machine
  §6.1    RunRecorder recording + observer projection

IPR constraints:
  IPR-0: invariants covered by test_loop_invariants.py (no new tests needed)
  IPR-1: corresponds to DESIGN.md §4.2.1 + §4.5 + §6.1
  IPR-3: stdlib + pydantic only, no model SDK
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from zall.core.action import Action
from zall.core.gate import (
    ConfirmGate,
    GateResult,
    GateState,
    UserResponse,
    UserResponseType,
)
from zall.core.safety import Judgement, SafeLevel, context_judge
from zall.core.verifiability import EventType

if TYPE_CHECKING:
    from zall.core.loop import AgentLoop


def _loop_event(*args: Any, **kwargs: Any) -> Any:
    """Lazy import LoopEvent to avoid circular import with loop.py."""
    from zall.core.loop_events import LoopEvent
    return LoopEvent(*args, **kwargs)


def _tool_not_found(*args: Any, **kwargs: Any) -> Any:
    """Lazy import ToolNotFound to avoid circular import with loop.py."""
    from zall.core.loop_errors import ToolNotFound
    return ToolNotFound(*args, **kwargs)


class ToolExecutor:
    """Executes tool calls through the full safety pipeline.

    For each tool call:
      1. Run context_judge (safety evaluation)
      2. Process through confirm_gate (state machine)
      3. Execute the tool
      4. Record result + checkpoint

    References the parent AgentLoop for shared state access.
    """

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop

    # ── Public API ──

    def execute_all(self, tool_calls: tuple, step_count: int) -> None:
        """Execute a batch of tool calls from a model response.

        Each tool call goes through the full safety pipeline independently.
        Results are appended to the loop's message list.
        """
        loop = self._loop
        for tc in tool_calls:
            action = Action(tool_id=tc.tool_id, args=tc.args)
            judgement = context_judge(action, loop._context, loop._rules)

            # Plan mode: write tools forced to GREYLIST
            if (
                loop._plan_mode
                and (action.tool_id in loop._WRITE_TOOLS
                     or _is_tool_write_by_kind(loop, action.tool_id))
                and judgement.level != SafeLevel.BLACKLIST
            ):
                judgement = Judgement(
                    level=SafeLevel.GREYLIST,
                    matched_rule_ids=("plan_mode_read_only",),
                )

            gate_result = self._process_gate(action, judgement, tc.tool_id, step_count)

            if gate_result is None:
                # SUSPENDED timeout → rejection already injected, continue
                continue

            self._execute_single(gate_result, tc.id if hasattr(tc, 'id') else None, step_count)

    # ── Gate state machine ──

    def _process_gate(
        self,
        action: Action,
        judgement: Judgement,
        tool_id: str,
        step_count: int,
    ) -> GateResult | None:
        """Full gate state machine for one tool call.

        Returns GateResult if execution is approved, None if rejected/suspended.
        """
        loop = self._loop
        loop._gate_decision_count += 1

        # Record gate decision event
        loop._recorder.append(
            event_id=f"gate_decision_{loop._gate_decision_count}",
            ts=int(time.time() * 1000),
            event_type=EventType.GATE_DECISION,
            payload={
                "tool_id": tool_id,
                "level": judgement.level.value,
                "matched_rules": list(judgement.matched_rule_ids),
            },
        )
        loop._emit(_loop_event(
            kind="gate_decision",
            step=step_count,
            payload={
                "tool_id": tool_id,
                "args": dict(action.args),
                "level": judgement.level.value,
                "matched_rules": list(judgement.matched_rule_ids),
            },
        ))

        gate = ConfirmGate(action, judgement)
        gate_result: GateResult = gate.process(None)

        _suspended_count = 0

        while True:
            state = gate_result.state

            if state in (GateState.AWAITING_USER, GateState.EQUIVALENCE_PROPOSED):
                user_resp = loop._user_responder.ask(
                    gate_result.action_to_execute or action,
                    judgement,
                )
                loop._recorder.append(
                    event_id=f"user_response_{loop._tool_call_count + 1}",
                    ts=int(time.time() * 1000),
                    event_type=EventType.USER_RESPONSE,
                    payload={"response_type": user_resp.response_type.value},
                )
                if user_resp.response_type == UserResponseType.OVERRIDE:
                    loop._recorder.append(
                        event_id=f"override_{loop._tool_call_count + 1}",
                        ts=int(time.time() * 1000),
                        event_type=EventType.OVERRIDE,
                        payload={
                            "tool_id": tool_id,
                            "override_text": user_resp.override_text or "",
                        },
                    )
                gate_result = _cast_gate_result(gate.process(user_resp))
                continue

            if state == GateState.SUSPENDED:
                _suspended_count += 1
                if _suspended_count >= 2:
                    loop._messages.append(
                        _make_suspended_rejection(tool_id, loop._tool_call_count + 1)
                    )
                    loop._emit(_loop_event(
                        kind="suspended",
                        step=step_count,
                        payload={"reason": "max_suspensions", "tool_id": tool_id},
                    ))
                    return None
                gate_result = _cast_gate_result(gate.process(UserResponse.resume()))
                continue

            if state == GateState.REJUDGE:
                new_action = gate_result.action_to_execute or action
                judgement = context_judge(new_action, loop._context, loop._rules)
                gate = ConfirmGate(new_action, judgement)
                gate_result = _cast_gate_result(gate.process(None))
                continue

            # EXECUTING / EXECUTING_WITH_OVERRIDE / REJECTED / TERMINAL
            break

        # Post-processing
        if gate_result.state == GateState.REJECTED:
            reason = gate_result.rejection_reason or "rejected by gate"
            loop._messages.append(_make_rejection_message(tool_id, reason, loop._tool_call_count + 1))
            loop._emit(_loop_event(
                kind="tool_rejected",
                step=step_count,
                payload={"tool_id": tool_id, "reason": reason},
            ))
            return None

        if gate_result.state == GateState.EXECUTING_WITH_OVERRIDE:
            if gate_result.override_event:
                loop._emit(_loop_event(
                    kind="override",
                    step=step_count,
                    payload={
                        "tool_id": tool_id,
                        "override_text": gate_result.override_event.override_text,
                    },
                ))

        return gate_result

    # ── Tool execution ──

    def _execute_single(self, gate_result: GateResult, call_id: str | None, step_count: int) -> None:
        """Execute one approved tool call and record the result."""
        loop = self._loop

        if gate_result.action_to_execute is None:
            raise RuntimeError(
                f"gate in state {gate_result.state} but no action_to_execute"
            )

        loop._tool_call_count += 1
        execute_action = gate_result.action_to_execute
        tid = execute_action.tool_id
        loop._tool_usage_counts[tid] = loop._tool_usage_counts.get(tid, 0) + 1

        tool = loop._tools.get(execute_action.tool_id)
        if tool is None:
            raise _tool_not_found(
                f"tool_id={execute_action.tool_id} not in ToolRegistry"
            )

        # Record tool_call_start
        loop._recorder.append(
            event_id=f"tool_call_start_{loop._tool_call_count}",
            ts=int(time.time() * 1000),
            event_type=EventType.TOOL_CALL_START,
            payload={
                "tool_id": tid,
                "args": dict(execute_action.args),
            },
        )
        loop._emit(_loop_event(
            kind="tool_call_start",
            step=step_count,
            payload={"tool_id": tid, "args": dict(execute_action.args)},
        ))

        # Execute
        try:
            result = tool.execute(execute_action.args)
        except Exception as e:
            from zall.core.tool import ToolResult
            result = ToolResult(
                success=False,
                output=f"[ERROR: tool raised {type(e).__name__}: {e}]",
                error=str(e),
            )

        # Record completion
        loop._recorder.append(
            event_id=f"tool_call_end_{loop._tool_call_count}",
            ts=int(time.time() * 1000),
            event_type=EventType.TOOL_CALL_END,
            payload={
                "tool_id": tid,
                "success": result.success,
                "output_length": len(result.output),
                "output": result.output,
                "error": result.error,
                "artifacts": dict(result.artifacts),
            },
        )
        loop._emit(_loop_event(
            kind="tool_call_end",
            step=step_count,
            payload={
                "tool_id": tid,
                "success": result.success,
                "output": result.output,
                "error": result.error,
                "artifacts": dict(result.artifacts),
            },
        ))

        # GitProtect checkpoint
        loop._maybe_checkpoint(tid, dict(execute_action.args))

        # Append tool result to messages
        from zall.core.model import Message, ToolCall
        _tc_id = call_id or f"call_{loop._tool_call_count}"
        _tool_call = ToolCall(id=_tc_id, tool_id=tid, args=dict(execute_action.args))
        loop._messages.append(Message.tool_result(
            content=result.output,
            tool_call_id=_tool_call.id,
            tool_id=_tool_call.tool_id,
        ))
        loop._mark_watermark_dirty()

        # Extension: on_after_tool (legacy) + on_tool_result (typed)
        if loop._ext_registry is not None:
            from zall.core.lifecycle import ToolResultInput
            _tr_input = ToolResultInput(
                tool_id=tid,
                success=result.success,
                output=result.output,
                error=result.error,
                step=step_count,
                duration=0.0,
                args=dict(execute_action.args),
            )
            loop._ext_registry.fire_all(
                "on_after_tool", "on_tool_result",
                typed_input=_tr_input,
                tool_id=tid,
                result=result,
                step=step_count,
            )


# ── Module-level helpers ──

def _make_rejection_message(tool_id: str, reason: str, call_index: int) -> Any:
    """Create a tool_result message for a rejected tool call."""
    from zall.core.model import Message, ToolCall
    _tc = ToolCall(id=f"gate_reject_{call_index}", tool_id=tool_id, args={})
    return Message.tool_result(
        content=f"[GATE REJECTED] tool '{tool_id}': {reason}",
        tool_call_id=_tc.id,
        tool_id=_tc.tool_id,
    )


def _make_suspended_rejection(tool_id: str, call_index: int) -> Any:
    """Create a tool_result message for a suspended tool call."""
    from zall.core.model import Message, ToolCall
    _tc = ToolCall(id=f"gate_suspend_{call_index}", tool_id=tool_id, args={})
    return Message.tool_result(
        content=f"[GATE SUSPENDED] tool '{tool_id}' timed out after 2 suspensions",
        tool_call_id=_tc.id,
        tool_id=_tc.tool_id,
    )


def _cast_gate_result(result: Any) -> GateResult:
    """Ensure the result is a GateResult (type narrowing helper)."""
    if isinstance(result, GateResult):
        return result
    raise TypeError(f"expected GateResult, got {type(result).__name__}: {result}")


def _is_tool_write_by_kind(loop: Any, tool_id: str) -> bool:
    """Check if a tool is a write-type tool via its ToolKind.
    
    Falls back to False if ToolKind is not available.
    """
    try:
        from zall.core.tool import get_tool_kind
        if loop._tools is not None:
            tool = loop._tools.get(tool_id)
            if tool is not None:
                return get_tool_kind(tool).is_write()
    except (ImportError, AttributeError):
        pass
    return False