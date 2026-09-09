"""zall.core.dynamic_inject — 按步动态注入 (kimi DynamicInjectionProvider 对标, 原创).

kimi soul/dynamic_injection.py + dynamic_injections/plan_mode.py 逐行研读后
按 zall 同步架构重写。核心巧思 (全部保留):

  1. Provider 协议: 每步模型调用前收集注入; 各 provider 自管节流。
  2. **历史推断节流** (kimi plan_mode 最有价值的设计): 不用独立计数器,
     反向扫描消息历史找上一条提醒、数其间 assistant 消息数 — 计数器与
     历史永不漂移; 压缩把旧提醒摘要掉后, 扫描自然找不到 → 自动重新注入
     (自愈, 无需 on_context_compacted 回调)。
  3. 稀疏/全文交替: 长会话里多数提醒用一行稀疏版, 周期性全文版, 省 token。

zall 首个 provider: PlanModeReminderProvider — plan 模式的只读纪律会随
回合数增长被模型淡忘 (实测长会话中后期开始试图写盘), 周期性重申。

IPR constraints:
  IPR-0: tests/test_dynamic_inject_invariants.py (含反例)
  IPR-3: stdlib + core, 无模型 SDK
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from zall.core.model import Message
from zall.core.verifiability import EventType

# 每 N 个 assistant 轮重申一次 (kimi 同款默认)
PLAN_REMINDER_INTERVAL = 5
# 每第 N 次提醒用全文版, 其余稀疏 (kimi 同款默认)
_FULL_EVERY_N = 5

# 稳定前缀 — 历史推断节流以它为锚 (改文案时保持前缀不变)
_PLAN_PREFIX = "[plan mode reminder]"

_PLAN_SPARSE = (
    f"{_PLAN_PREFIX} Plan mode still active: read-only tools only "
    "(read_file/grep/glob/list_dir). Do not create, modify or delete anything."
)
_PLAN_FULL = (
    f"{_PLAN_PREFIX} Plan mode is ACTIVE. You are in analysis-first, "
    "read-only mode:\n"
    "  - Use read-only tools freely (read_file, grep, glob, list_dir).\n"
    "  - Do NOT create, modify, or delete files; no write-effect bash.\n"
    "  - Structure your output: Understanding -> Analysis -> Proposed Changes\n"
    "    with exact file paths and code patterns.\n"
    "  - The user reviews your plan before authorizing execution."
)


class DynamicInjectionProvider(Protocol):
    """按步注入 provider 协议 — get_injections 自管节流, 返回注入文本列表。"""

    def get_injections(self, loop: Any) -> list[str]: ...


class PlanModeReminderProvider:
    """plan 模式只读纪律周期性重申 (历史推断节流)。"""

    __test__ = False

    def __init__(self) -> None:
        self._inject_count = 0

    def get_injections(self, loop: Any) -> list[str]:
        if not getattr(loop, "plan_mode", False):
            self._inject_count = 0
            return []

        # 历史推断节流: 反向扫描找上一条提醒, 数其间 assistant 消息
        turns_since = 0
        found_previous = False
        for m in reversed(loop.messages):
            if m.role == "system" and (m.content or "").startswith(_PLAN_PREFIX):
                found_previous = True
                break
            if m.role == "assistant":
                turns_since += 1

        if not found_previous:
            # 首次 (或压缩把旧提醒摘要掉了) → 全文版, 自愈重注入
            self._inject_count = 1
            return [_PLAN_FULL]
        if turns_since < PLAN_REMINDER_INTERVAL:
            return []
        self._inject_count += 1
        is_full = self._inject_count % _FULL_EVERY_N == 1
        return [_PLAN_FULL if is_full else _PLAN_SPARSE]


def run_injections(loop: Any) -> None:
    """每步模型调用前执行全部 provider (loop 协作者自由函数)。

    注入以 system 消息追加, 并记 SYSTEM_INJECTION 到 timeline (§6.1 全保真)。
    任一 provider 异常 → 静默跳过 (IPR-0: 注入失败不得影响主流程)。
    """
    providers = getattr(loop, "_injection_providers", None)
    if not providers:
        return
    for provider in providers:
        try:
            texts = provider.get_injections(loop)
        except Exception:
            continue
        for text in texts:
            loop._append_message(Message(role="system", content=text))
            loop._recorder.append(
                event_id=f"dyn_inject_{loop._step_count}_{type(provider).__name__}",
                ts=int(time.time() * 1000),
                event_type=EventType.SYSTEM_INJECTION,
                payload={
                    "reason": "dynamic_injection",
                    "provider": type(provider).__name__,
                    "text": text[:200],
                },
            )


__all__ = [
    "PLAN_REMINDER_INTERVAL",
    "DynamicInjectionProvider",
    "PlanModeReminderProvider",
    "run_injections",
]
