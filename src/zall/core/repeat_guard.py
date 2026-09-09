"""zall.core.repeat_guard — 工具重复调用梯度惩罚 (kimi toolset dedup/repeat 对标, 原创).

kimi soul/toolset.py 的重复检测机制逐行研读后按 zall 同步架构重写:

  连击追踪 (跨步): 同一 (tool_id, 规范化参数) 被连续调用 —
    streak >= 3  → r1  轻提醒 (追加进工具结果: 检查结果、换动作)
    streak >= 5  → r2  重提醒 (点名重复次数与参数, 禁止原样重发)
    streak >= 8  → r3  勒令停止 (只允许文字总结)
    streak >= 12 → stop 强制结束本回合 (模型已听不进提醒, 止损)

  同步内去重 (单步): 同一步内完全相同的调用只执行一次,
    重复者不再执行, 直接得到"重复调用"占位结果 (省时省 token)。

与 doom-loop 检测的关系: doom-loop 看的是"步级工具序列哈希"重复 (粗粒度,
单独的 system nudge); repeat_guard 看的是"单个调用连击" (细粒度, 提醒直接
追加在工具结果里 — kimi 实证该位置模型更听得进)。两者互补共存。

IPR constraints:
  IPR-0: tests/test_repeat_guard_invariants.py (含反例)
  IPR-3: stdlib only
"""

from __future__ import annotations

import json
from typing import Any

# 升级阈值 (kimi 同款默认: 3/5/8/12)
R1_START = 3
R2_START = 5
R3_START = 8
FORCE_STOP_STREAK = 12

_R1_TEXT = (
    "\n\n[repeat notice] You have called this exact tool with these exact "
    "arguments {n} times in a row. Inspect the result above carefully — if it "
    "already answers your question, act on it; otherwise change the tool or "
    "the arguments. Do not repeat the identical call."
)
_R2_TEXT = (
    "\n\n[repeat warning] Identical call repeated {n} times: tool={tool} "
    "args={args}. The previous repeats made no progress. You MUST choose a "
    "different action, different arguments, or finish the task with the "
    "evidence already gathered. Repeating this exact call again is forbidden."
)
_R3_TEXT = (
    "\n\n[repeat dead-end] You are stuck: the same call has been repeated {n} "
    "times without progress. STOP calling tools now. In your next response, "
    "return TEXT ONLY: report the current problem, what was tried, and what "
    "information or decision is needed to proceed."
)


def canonical_key(tool_id: str, args: dict[str, Any] | None) -> str:
    """(tool_id, 参数) → 规范化键 (键序无关; 不可序列化参数降级 repr)。"""
    try:
        blob = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):
        blob = repr(sorted((args or {}).items(), key=lambda kv: kv[0]))
    return f"{tool_id}::{blob}"


class RepeatGuard:
    """单调用粒度的连击追踪 + 梯度提醒。

    note_call() 在每次工具真实执行后调用, 返回 (action, reminder):
      action ∈ none|r1|r2|r3|stop; reminder 为追加进工具结果的文本 (或 None)。
    stop 时置 force_stop 标志, 由 AgentLoop 在本步末尾优雅结束回合。
    """

    __test__ = False

    def __init__(self) -> None:
        self._key: str | None = None
        self._count: int = 0
        self.force_stop: bool = False

    @property
    def streak(self) -> int:
        return self._count

    def reset(self) -> None:
        """新回合 / context_rewind 后复位 (kimi begin_step 空列表复位对标)。"""
        self._key = None
        self._count = 0
        self.force_stop = False

    def note_call(
        self, tool_id: str, args: dict[str, Any] | None,
    ) -> tuple[str, str | None]:
        """记录一次调用并返回 (action, reminder_text)。"""
        key = canonical_key(tool_id, args)
        if key == self._key:
            self._count += 1
        else:
            self._key = key
            self._count = 1

        n = self._count
        if n >= FORCE_STOP_STREAK:
            self.force_stop = True
            return "stop", _R3_TEXT.format(n=n)
        if n >= R3_START:
            return "r3", _R3_TEXT.format(n=n)
        if n >= R2_START:
            args_preview = key.split("::", 1)[1][:160]
            return "r2", _R2_TEXT.format(n=n, tool=tool_id, args=args_preview)
        if n >= R1_START:
            return "r1", _R1_TEXT.format(n=n)
        return "none", None


# 同步内去重占位结果 (kimi same-step dedup 对标; zall 顺序执行 → 直接跳过执行)
DUPLICATE_CALL_NOTE = (
    "[duplicate tool call within the same step — identical to an earlier call "
    "above; not re-executed. Use the earlier result. Do not emit identical "
    "duplicate calls in one response.]"
)


__all__ = [
    "DUPLICATE_CALL_NOTE",
    "FORCE_STOP_STREAK",
    "R1_START",
    "R2_START",
    "R3_START",
    "RepeatGuard",
    "canonical_key",
]
