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
        keep_recent: 压缩保留的最近消息数 (默认 4)
        min_compaction_interval: 最小压缩间隔步数 (默认 5)
    """
    auto_compact_threshold_percent: int = 85
    compact_model: str | None = None
    wall_clock_budget_secs: int = 300
    two_pass_enabled: bool = False
    memory_flush_enabled: bool = False
    keep_recent: int = 4
    min_compaction_interval: int = 5

    def __post_init__(self) -> None:
        """Validate thresholds."""
        if not 50 <= self.auto_compact_threshold_percent <= 99:
            raise ValueError(
                f"auto_compact_threshold_percent must be 50-99, "
                f"got {self.auto_compact_threshold_percent}"
            )
        if self.keep_recent < 1:
            raise ValueError(f"keep_recent must be >= 1, got {self.keep_recent}")
        if self.min_compaction_interval < 1:
            raise ValueError(
                f"min_compaction_interval must be >= 1, "
                f"got {self.min_compaction_interval}"
            )

    # ── 预置策略 ──

    @classmethod
    def conservative(cls) -> CompactionPolicy:
        """保守策略: 较早触发压缩, 保留更多上下文。"""
        return cls(
            auto_compact_threshold_percent=70,
            keep_recent=6,
            min_compaction_interval=8,
        )

    @classmethod
    def aggressive(cls) -> CompactionPolicy:
        """激进策略: 较晚触发压缩, 保留更少上下文。"""
        return cls(
            auto_compact_threshold_percent=92,
            keep_recent=2,
            min_compaction_interval=3,
        )


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