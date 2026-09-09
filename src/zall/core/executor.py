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


# ── 上下文入口中心截断 (context ingestion cap) ──
# 背景: 各工具自身有宽松上限 (read_file 2000 行 / bash 50KB / grep 200 匹配),
# 但工具输出追加进消息历史时**无中心上限** — 首轮对话就能把上下文堆到
# 数十万 token, 每次 model call 全量重发 → 响应超慢 + 成本爆炸。
# timeline/event 仍记录完整输出 (磁盘便宜); 只有**进入模型上下文的副本**被截断。
# head+tail 保留: 头部是主体信息, 尾部常含总结/错误行。
MAX_CONTEXT_TOOL_OUTPUT = 32_000  # chars (≈ 8K tokens)
_CAP_HEAD = 26_000
_CAP_TAIL = 4_000


def clip_tool_output_for_context(output: str) -> str:
    """截断进入消息历史的工具输出 (保头保尾 + 显式截断提示)。

    不变量 (I-CTX-CAP, test_tool_invariants):
      - len(result) 不超 MAX_CONTEXT_TOOL_OUTPUT + 提示长度
      - 未超限输出原样返回 (无副作用)
      - 截断时保留头部与尾部, 中间插入可见提示 (模型可感知截断并用
        offset/limit 或更精确的 grep 补读)
    """
    if len(output) <= MAX_CONTEXT_TOOL_OUTPUT:
        return output
    omitted = len(output) - _CAP_HEAD - _CAP_TAIL
    return (
        output[:_CAP_HEAD]
        + f"\n... [context cap: {omitted} chars omitted of {len(output)} total — "
        "re-run with a narrower range (offset/limit or a more specific pattern) "
        "if you need the omitted part] ...\n"
        + output[-_CAP_TAIL:]
    )


def spill_full_output(loop: Any, tool_id: str, output: str) -> str | None:
    """超限工具输出全量落盘 (kimi Background 输出协议对标)。

    kimi 的巧思: 截断提示里给出**精确的文件路径 + 分页读取指引** —
    bash 等不可重放的输出 (副作用已发生) 也能事后补读, 而非永久丢失。
    落盘到 .zall/tool_outputs/ (项目级, 随 .zall 已在 gitignore 惯例)。
    IPR-0: 任何失败返回 None (降级为纯截断提示, 不影响主流程)。
    """
    try:
        from pathlib import Path
        root = Path(getattr(getattr(loop, "_context", None), "cwd_meta", None)
                    and loop._context.cwd_meta.cwd_path or ".")
        out_dir = root / ".zall" / "tool_outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"step{loop._step_count}_call{loop._tool_call_count}_{tool_id}.txt"
        fpath = out_dir / fname
        fpath.write_text(output, encoding="utf-8", errors="replace")
        return str(fpath)
    except Exception:
        return None


def clip_with_spill(loop: Any, tool_id: str, output: str) -> str:
    """中心截断 + 超限时全量落盘并附分页读取指引。"""
    clipped = clip_tool_output_for_context(output)
    if clipped is output:
        return output
    spill_path = spill_full_output(loop, tool_id, output)
    if spill_path:
        clipped += (
            f"\n[full output saved to: {spill_path} — use "
            f'read_file(path="{spill_path}", offset=..., limit=...) '
            "to page through the omitted part]"
        )
    return clipped


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

        同步内去重 (kimi same-step dedup 对标): 同一步内完全相同的调用
        只执行一次, 重复者直接得到占位结果 (不过门不执行, 省时省 token;
        每个 tool_call id 仍得到配对的 tool 消息, API 契约不破)。
        """
        from zall.core.repeat_guard import DUPLICATE_CALL_NOTE, canonical_key
        loop = self._loop
        seen_in_step: set[str] = set()
        for tc in tool_calls:
            key = canonical_key(tc.tool_id, dict(tc.args or {}))
            if key in seen_in_step:
                loop._tool_call_count += 1
                loop._recorder.append(
                    event_id=f"tool_call_end_{loop._tool_call_count}",
                    ts=int(time.time() * 1000),
                    event_type=EventType.TOOL_CALL_END,
                    payload={
                        "tool_id": tc.tool_id,
                        "success": True,
                        "output_length": len(DUPLICATE_CALL_NOTE),
                        "output": DUPLICATE_CALL_NOTE,
                        "error": None,
                        "artifacts": {"dedup": "same_step"},
                    },
                )
                loop._emit(_loop_event(
                    kind="tool_call_end",
                    step=step_count,
                    payload={
                        "tool_id": tc.tool_id,
                        "success": True,
                        "output": DUPLICATE_CALL_NOTE,
                        "error": None,
                        "artifacts": {"dedup": "same_step"},
                    },
                ))
                from zall.core.model import Message
                loop.append_message(Message.tool_result(
                    content=DUPLICATE_CALL_NOTE,
                    tool_call_id=(tc.id if hasattr(tc, "id")
                                  else f"call_{loop._tool_call_count}"),
                    tool_id=tc.tool_id,
                ))
                continue
            seen_in_step.add(key)
            action = Action(tool_id=tc.tool_id, args=tc.args)
            # v0.5.1: 传入 tool_registry, 让 context_judge 根据工具能力决定默认权限
            judgement = context_judge(
                action, loop._context, loop._rules,
                tool_registry=loop._tools,
            )

            # Plan mode: 写工具强制 GREYLIST (使用 PlanModeTracker + ToolCapabilities)
            if loop._planner.is_active and judgement.level not in (
                SafeLevel.BLACKLIST, SafeLevel.GREYLIST,
            ):
                # 检查工具能力: 非只读工具在 plan mode 下强制 GREYLIST
                _tool = loop._tools.get(action.tool_id) if loop._tools else None
                if _tool is not None:
                    from zall.core.tool import get_tool_capabilities
                    _caps = get_tool_capabilities(_tool)
                    if not _caps.is_read_only:
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
        _rejudge_count = 0
        _MAX_REJUDGE = 5
        _SUSPENDED_TIMEOUT = 300.0  # 5 minutes total timeout for SUSPENDED
        _suspended_start: float | None = None

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
                if _suspended_start is None:
                    _suspended_start = time.time()
                # v0.5.0 (C1 fix): 增加 SUSPENDED 整体超时机制
                elapsed = time.time() - _suspended_start
                if _suspended_count >= 2 or elapsed >= _SUSPENDED_TIMEOUT:
                    reason = "max_suspensions" if _suspended_count >= 2 else "suspended_timeout"
                    loop.append_message(
                        _make_suspended_rejection(tool_id, loop._tool_call_count + 1)
                    )
                    loop._emit(_loop_event(
                        kind="suspended",
                        step=step_count,
                        payload={"reason": reason, "tool_id": tool_id},
                    ))
                    return None
                gate_result = _cast_gate_result(gate.process(UserResponse.resume()))
                continue

            if state == GateState.REJUDGE:
                _rejudge_count += 1
                if _rejudge_count >= _MAX_REJUDGE:
                    # Max rejudge attempts reached — reject to prevent infinite loop
                    loop.append_message(_make_rejection_message(
                        tool_id,
                        f"rejudge limit ({_MAX_REJUDGE}) exceeded",
                        loop._tool_call_count + 1,
                    ))
                    loop._emit(_loop_event(
                        kind="tool_rejected",
                        step=step_count,
                        payload={"tool_id": tool_id, "reason": f"rejudge limit ({_MAX_REJUDGE}) exceeded"},
                    ))
                    return None
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
            loop.append_message(_make_rejection_message(tool_id, reason, loop._tool_call_count + 1))
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

        tool = loop._tools.get(execute_action.tool_id)
        if tool is None:
            raise _tool_not_found(
                f"tool_id={execute_action.tool_id} not in ToolRegistry"
            )
        # 计数放在存在性校验之后: 幻觉 tool_id 不会在计数表里留下新 key
        # (doom-loop 场景下模型编造任意 tool_id 会让 dict 无限涨 key)
        loop._tool_usage_counts[tid] = loop._tool_usage_counts.get(tid, 0) + 1

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

        # v0.5.1: 运行时 JSON Schema 校验 (MASTER.md §12.1 Plugin: schema 强制校验)
        from zall.core.tool import validate_tool_args
        validation_errors = validate_tool_args(tool.schema, dict(execute_action.args))
        if validation_errors:
            err_msg = "invalid args: " + "; ".join(validation_errors)
            from zall.core.tool import ToolResult
            result = ToolResult(
                success=False,
                output=f"[SCHEMA VALIDATION FAILED] {err_msg}",
                error=err_msg,
            )
            # 追加一条独立的校验失败记录到 timeline。
            # M1 fix: 必须用唯一 event_id (不能复用上面的 tool_call_start_{count}),
            # 否则 timeline 出现两条同 event_id 事件, 破坏 replay/去重 (event_id 唯一不变量)。
            loop._recorder.append(
                event_id=f"tool_call_validation_fail_{loop._tool_call_count}",
                ts=int(time.time() * 1000),
                event_type=EventType.TOOL_CALL_START,
                payload={
                    "tool_id": tid,
                    "args": dict(execute_action.args),
                    "validation_errors": validation_errors,
                },
            )
            # 直接跳到结果处理, 不执行工具
            loop._emit(_loop_event(
                kind="tool_call_start",
                step=step_count,
                payload={
                    "tool_id": tid,
                    "args": dict(execute_action.args),
                    "validation_errors": validation_errors,
                },
            ))
            # 直接录制结果并返回
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
                    "artifacts": {},
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
                    "artifacts": {},
                },
            ))
            # Append tool result to messages (上下文副本经中心截断 + 重复提醒)
            from zall.core.model import Message, ToolCall
            _ra, _reminder = loop._repeat_guard.note_call(tid, dict(execute_action.args))
            _content = clip_tool_output_for_context(result.output)
            if _reminder:
                _content += _reminder
            _tc_id = call_id or f"call_{loop._tool_call_count}"
            _tool_call = ToolCall(id=_tc_id, tool_id=tid, args=dict(execute_action.args))
            loop.append_message(Message.tool_result(
                content=_content,
                tool_call_id=_tool_call.id,
                tool_id=_tool_call.tool_id,
            ))
            loop._mark_watermark_dirty()
            return

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

        # Append tool result to messages (上下文副本经中心截断 + 重复梯度提醒;
        # timeline 保留全量)。提醒直接追加在工具结果内 (kimi 实证该位置模型更听
        # 得进, 优于独立 system 消息)。
        from zall.core.model import Message, ToolCall
        _ra, _reminder = loop._repeat_guard.note_call(tid, dict(execute_action.args))
        _content = clip_with_spill(loop, tid, result.output)
        if _reminder:
            _content += _reminder
            loop._emit(_loop_event(
                kind="repeat_warning",
                step=step_count,
                payload={"tool_id": tid, "action": _ra,
                         "streak": loop._repeat_guard.streak},
            ))
        _tc_id = call_id or f"call_{loop._tool_call_count}"
        _tool_call = ToolCall(id=_tc_id, tool_id=tid, args=dict(execute_action.args))
        loop.append_message(Message.tool_result(
            content=_content,
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