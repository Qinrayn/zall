"""按步动态注入 (dynamic_inject, kimi DynamicInjectionProvider 对标) 不变量测试.

IPR-0: each test must contain a counterexample.

Protected invariants:
  I-DYNINJ-1: plan 模式关闭 → 零注入 (反例孪生: 开启后首步必注全文版)。
  I-DYNINJ-2: 历史推断节流 — 上一条提醒后不足 N 个 assistant 轮 → 不重复注入;
              达到 N 轮 → 重申 (稀疏版)。
  I-DYNINJ-3: 压缩自愈 — 历史中提醒被摘要掉 (不见前缀) → 自动重新注入全文版,
              无需任何回调。
  I-DYNINJ-4: provider 异常静默跳过 (IPR-0); 注入记 SYSTEM_INJECTION 到 timeline。
"""

from __future__ import annotations

import pytest

from zall.core.dynamic_inject import (
    PLAN_REMINDER_INTERVAL,
    PlanModeReminderProvider,
    run_injections,
)
from zall.core.model import Message


class _FakeLoop:
    """最小 loop 桩: messages + plan_mode + 注入接口。"""

    __test__ = False

    def __init__(self, plan_mode: bool = True) -> None:
        self.plan_mode = plan_mode
        self.messages: list[Message] = []
        self._step_count = 1
        self._injection_providers: list = []
        self.timeline_payloads: list[dict] = []
        self._recorder = self

    # recorder 接口
    def append(self, **kw) -> None:
        self.timeline_payloads.append(kw.get("payload", {}))

    def _append_message(self, m: Message) -> None:
        self.messages.append(m)


def _reminders(loop: _FakeLoop) -> list[str]:
    return [m.content for m in loop.messages
            if m.role == "system" and (m.content or "").startswith("[plan mode reminder]")]


# ── I-DYNINJ-1 ──


def test_no_injection_when_plan_off() -> None:
    loop = _FakeLoop(plan_mode=False)
    p = PlanModeReminderProvider()
    assert p.get_injections(loop) == []


def test_first_injection_is_full_version() -> None:
    """反例孪生: plan 开启且史上无提醒 → 必注全文版。"""
    loop = _FakeLoop(plan_mode=True)
    p = PlanModeReminderProvider()
    texts = p.get_injections(loop)
    assert len(texts) == 1
    assert "Plan mode is ACTIVE" in texts[0]


# ── I-DYNINJ-2: 历史推断节流 ──


def test_throttle_by_assistant_turns_in_history() -> None:
    loop = _FakeLoop(plan_mode=True)
    p = PlanModeReminderProvider()
    # 首注
    for t in p.get_injections(loop):
        loop._append_message(Message(role="system", content=t))
    # 不足 N 个 assistant 轮 → 不注 (反例)
    for _ in range(PLAN_REMINDER_INTERVAL - 1):
        loop._append_message(Message.assistant(content="step"))
        assert p.get_injections(loop) == [], "throttle must hold below interval"
    # 达到 N 轮 → 重申
    loop._append_message(Message.assistant(content="step"))
    texts = p.get_injections(loop)
    assert len(texts) == 1
    assert "still active" in texts[0]        # 第 2 次是稀疏版


# ── I-DYNINJ-3: 压缩自愈 ──


def test_compaction_self_heal_reinjects_full() -> None:
    loop = _FakeLoop(plan_mode=True)
    p = PlanModeReminderProvider()
    for t in p.get_injections(loop):
        loop._append_message(Message(role="system", content=t))
    # 模拟压缩: 历史被替换, 提醒被摘要掉
    loop.messages = [Message(role="user", content="[compacted summary]")]
    texts = p.get_injections(loop)
    assert len(texts) == 1
    assert "Plan mode is ACTIVE" in texts[0]  # 自愈: 全文版重注


# ── I-DYNINJ-4: run_injections 集成 ──


def test_run_injections_appends_and_records() -> None:
    loop = _FakeLoop(plan_mode=True)
    loop._injection_providers = [PlanModeReminderProvider()]
    run_injections(loop)
    assert len(_reminders(loop)) == 1
    assert any(p.get("reason") == "dynamic_injection"
               for p in loop.timeline_payloads)


def test_broken_provider_skipped_silently() -> None:
    """IPR-0 反例: 坏 provider 不得让注入流程崩溃, 其余 provider 照常。"""
    class _Boom:
        def get_injections(self, loop):
            raise RuntimeError("provider exploded")

    loop = _FakeLoop(plan_mode=True)
    loop._injection_providers = [_Boom(), PlanModeReminderProvider()]
    run_injections(loop)                      # 不抛
    assert len(_reminders(loop)) == 1         # 好 provider 仍生效


def test_no_providers_is_noop() -> None:
    loop = _FakeLoop(plan_mode=True)
    loop._injection_providers = []
    run_injections(loop)
    assert loop.messages == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
