"""zall.core.policies — First-class policy configurations.

Inspired by Grok Build's CompactionPolicy and ReminderPolicy.
Centralizes policy configuration that was previously embedded in AgentLoop.

Usage:
    from zall.core.policies import CompactionPolicy, ReminderPolicy

    config = AgentConfig(
        compaction_policy=CompactionPolicy(
            auto_compact_threshold_percent=80,
            wall_clock_budget_secs=300,
        ),
        reminder_policy=ReminderPolicy(enabled=True),
    )

IPR constraints:
  IPR-3: stdlib only, no model SDK
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CompactionPolicy:
    """上下文压缩策略 — 控制何时/如何压缩 model context window。

    Inspired by Grok Build's xai-grok-agent compaction policy.
    控制压缩阈值、使用的模型、wall-clock 预算等。

    Attributes:
        auto_compact_threshold_percent: 触发自动压缩的 context 使用率百分比 (默认 85%)
        compact_model: 用于生成压缩摘要的模型名称 (None = 使用当前会话模型)
        wall_clock_budget_secs: 每次压缩的 wall-clock 预算 (秒), 超时截断
        two_pass_enabled: 启用双通道压缩 (预压缩历史 + 最终压缩)
        memory_flush_enabled: 压缩前是否执行 memory flush turn
    """
    auto_compact_threshold_percent: int = 85
    compact_model: str | None = None
    wall_clock_budget_secs: int = 300
    two_pass_enabled: bool = False
    memory_flush_enabled: bool = False


@dataclass
class ReminderPolicy:
    """系统提醒策略 — 控制 agent 系统提醒的注入方式。

    Inspired by Grok Build's xai-grok-agent system_reminder system.
    替代 loop.py 中硬编码的 _EMPTY_STOP_NUDGE 和 ad-hoc 提醒。

    Attributes:
        enabled: 是否启用系统提醒
        max_reminders_per_session: 每 session 最大提醒次数
        empty_stop_nudge_enabled: 是否启用空回复提醒 (原 _EMPTY_STOP_NUDGE)
        custom_reminders: 自定义提醒列表
    """
    enabled: bool = True
    max_reminders_per_session: int = 5
    empty_stop_nudge_enabled: bool = True
    custom_reminders: tuple[str, ...] = field(default_factory=tuple)