"""zall.core.loop_rewind — context_rewind 的落锚与施加 (loop 协作者模块).

kimi kimisoul._agent_loop 的 checkpoint/BackToTheFuture 段逐行研读后按 zall
同步架构原创重写。与 loop_perception / loop_checkpoint / loop_model_call
同款协作者模式: 无状态自由函数, 接收 loop 实例。

行为:
  maybe_drop_anchor(loop)   — 每步开始时落 [CHECKPOINT k] 可见锚点
                              (仅当 context_rewind 工具已注册; 未注册零成本)
  apply_pending_rewind(loop) — 工具执行后检查信箱; 有请求则:
      1. timeline 先记 CONTEXT_REWIND (§6.1 timeline 是真相源, 先记后改)
      2. 截断消息列表回锚点长度
      3. 追加"给过去自己的信" (user 消息, 模板包裹)
      4. 丢弃该锚点之后的锚点; 重置 doom-loop 追踪 (kimi begin_step 复位对标)
      5. 广播 context_rewind 事件 (呈现层可显示折叠提示)

不变量 (tests/test_context_rewind_invariants.py, 含反例):
  - 未注册工具 → 不落锚、消息列表不含 [CHECKPOINT] 标记
  - 回滚后消息长度 == 锚点长度 + 1 (信); 锚点之后的锚点被丢弃
  - timeline 含 CONTEXT_REWIND 事件且早于消息截断
  - 无效 checkpoint_id → 工具报错, 上下文不变

IPR-3: stdlib + core 模块, 无模型 SDK。
"""

from __future__ import annotations

import time
from typing import Any

from zall.core.loop_events import LoopEvent
from zall.core.model import Message
from zall.core.verifiability import EventType

# 锚点标记模板 — 模型可见 (工具 schema 引用 [CHECKPOINT k] 字样)
_ANCHOR_TEXT = "[CHECKPOINT {k}]"
_REWIND_LETTER_TEXT = (
    "[Context rewound to CHECKPOINT {k} by your own context_rewind call. "
    "Everything after that checkpoint was folded into this letter from your "
    "future self — do not repeat work it says is already done.]\n{message}"
)
_REWIND_REFUSED_TEXT = (
    "[context_rewind to CHECKPOINT {k} was NOT applied: {reason}. "
    "The context was NOT folded — treat everything you currently see as still "
    "live. Your letter is preserved below; re-issue the rewind against a "
    "checkpoint marker you can still see, if one exists.]\n{message}"
)


def _get_rewind_tool(loop: Any) -> Any:
    """惰性查找已注册的 context_rewind 工具 (缓存查找结果)。"""
    if not loop._rewind_tool_checked:
        loop._rewind_tool_checked = True
        tool = loop._tools.get("context_rewind") if loop._tools is not None else None
        # 只认带 mailbox 的实现 (防插件同名工具混入)
        if tool is not None and hasattr(tool, "mailbox"):
            # 进程级单例工具跨 loop 共享 mailbox: 新 loop 首次绑定时清掉
            # 上一个 loop (被中断/放弃) 遗留的未处理请求 (跨会话串味防护)
            tool.mailbox.reset()
            loop._rewind_tool = tool
        else:
            loop._rewind_tool = None
    return loop._rewind_tool


def maybe_drop_anchor(loop: Any) -> None:
    """每步开始时落一个可见锚点 (工具未注册时 no-op, 不进热路径)。"""
    tool = _get_rewind_tool(loop)
    if tool is None:
        return
    k = len(loop._rewind_anchors)
    loop._append_message(Message(role="system", content=_ANCHOR_TEXT.format(k=k)))
    # 锚点长度 = 含标记本身 (回滚后模型仍能看到该 checkpoint 标记)
    loop._rewind_anchors.append(len(loop._chat_state.messages))
    tool.mailbox.set_n_anchors(len(loop._rewind_anchors))


def apply_pending_rewind(loop: Any) -> bool:
    """工具执行后施加待处理回滚; 返回是否发生了回滚。"""
    tool = _get_rewind_tool(loop)
    if tool is None:
        return False
    req = tool.mailbox.fetch()
    if req is None:
        return False

    anchors: list[int] = loop._rewind_anchors
    messages = loop.messages
    refused_reason: str | None = None
    if not (0 <= req.checkpoint_id < len(anchors)):
        # 信箱已校验过; 双保险 (锚点列表被外部动过时不崩)
        refused_reason = f"unknown checkpoint {req.checkpoint_id}"
    elif anchors[req.checkpoint_id] > len(messages):
        # 压缩把消息列表裁得比锚点短 → 锚点失效。
        # 不吞"信": 注入明确失败说明, 模型不会按"已折叠"的假象继续 (P2 fix)。
        refused_reason = ("the context was compacted past that anchor, so the "
                          "checkpoint no longer exists in this conversation")
    if refused_reason is not None:
        # timeline 先记 (守 §6.1 先记后改; refused 也留审计)
        loop._recorder.append(
            event_id=f"context_rewind_refused_{loop._step_count}",
            ts=int(time.time() * 1000),
            event_type=EventType.CONTEXT_REWIND,
            payload={
                "step": loop._step_count,
                "checkpoint_id": req.checkpoint_id,
                "refused": True,
                "reason": refused_reason,
                "message_preview": req.message[:200],
            },
        )
        loop._append_message(Message(role="system", content=(
            _REWIND_REFUSED_TEXT.format(
                k=req.checkpoint_id, reason=refused_reason, message=req.message,
            )
        )))
        return False

    anchor_len = anchors[req.checkpoint_id]
    dropped = len(messages) - anchor_len

    # 1. timeline 先记 (§6.1: 先记后改, 记录失败则不动消息)
    loop._recorder.append(
        event_id=f"context_rewind_{loop._step_count}",
        ts=int(time.time() * 1000),
        event_type=EventType.CONTEXT_REWIND,
        payload={
            "step": loop._step_count,
            "checkpoint_id": req.checkpoint_id,
            "dropped_count": dropped,
            "message_preview": req.message[:200],
        },
    )

    # 2. 截断 + 3. 追加"给过去自己的信"
    loop.set_messages(list(messages[:anchor_len]))
    loop._append_message(Message.user(
        _REWIND_LETTER_TEXT.format(k=req.checkpoint_id, message=req.message)
    ))

    # 4. 锚点收缩 + doom-loop/重复连击追踪复位 (kimi begin_step 空列表复位对标:
    #    回滚后的"重复调用"是新时间线的第一次, 不该继承旧连击计数)
    del anchors[req.checkpoint_id + 1:]
    tool.mailbox.set_n_anchors(len(anchors))
    loop._tool_seq_history.clear()
    loop._repeat_guard.reset()

    # 5. 广播 (呈现层显示折叠提示)
    loop._emit(LoopEvent(
        kind="context_rewind",
        step=loop._step_count,
        payload={
            "checkpoint_id": req.checkpoint_id,
            "dropped_count": dropped,
            "message_preview": req.message[:120],
        },
    ))
    return True


__all__ = ["apply_pending_rewind", "maybe_drop_anchor"]
