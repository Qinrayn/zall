"""zall.core.loop_config — AgentConfig + _GitProtectProtocol."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from zall.core.accountability import Judge
from zall.core.chat_state import ChatState
from zall.core.checkpoint import CheckpointManager
from zall.core.compactor import Compactor
from zall.core.events import EventBus
from zall.core.extension import ExtensionRegistry
from zall.core.policies import CompactionPolicy, ReminderPolicy
from zall.core.verifiability import TrustAnchor


@runtime_checkable
class _GitProtectProtocol(Protocol):
    def is_git_repo(self) -> bool: ...
    def checkpoint(self, label: str = "") -> dict[str, Any] | None: ...
    def rollback(self, to_index: int | None = None) -> bool: ...

@dataclass(frozen=True)
class AgentConfig:
    judge: Judge | None = None
    # judges dict (§12.1 多 Judge 编排): key=judge_type ("system"/"user"/"model_self")
    # 优先于 judge 字段; 与 judge 共存以实现向后兼容
    judges: dict[str, Judge] | None = None
    observer: Callable[..., None] | None = None
    event_bus: EventBus | None = None
    max_steps: int | None = None
    stream: bool | None = None
    git_protect: _GitProtectProtocol | None = None
    checkpoint_mgr: CheckpointManager | None = None
    allow_downgrade: bool | None = None
    plan_mode: bool | None = None
    compactor: Compactor | None = None
    anchor: TrustAnchor | None = None
    ext_registry: ExtensionRegistry | None = None
    chat_state: ChatState | None = None
    # Phase 4: First-class policies
    compaction_policy: CompactionPolicy | None = None
    reminder_policy: ReminderPolicy | None = None
    # v0.5.1: Strict mode — when True, enable full confirm/downgrade gates;
    # when False (default), auto-confirm goals and auto-skip downgrade popups.
    strict: bool = False
    # v0.5.1: PlanModeTracker instance (替代纯 plan_mode bool)
    planner: Any | None = None
    # v0.6.0: Perception Engine (MASTER.md §4.2.3)
    perception_engine: Any | None = None
    # v0.6.x: Identity (MASTER.md §1.2 + §4.2.1) — 六维本体论 ① Identity 维度
    identity: Any | None = None

    @classmethod
    def from_kwargs(cls, judge=None, observer=None, event_bus=None,
                    max_steps=None, stream=None, git_protect=None,
                    checkpoint_mgr=None, allow_downgrade=None, plan_mode=None,
                    compactor=None, anchor=None, ext_registry=None,
                    chat_state=None, compaction_policy=None,
                    reminder_policy=None, strict=None,
                    planner=None, perception_engine=None, identity=None) -> AgentConfig:
        return cls(judge=judge, observer=observer, event_bus=event_bus,
                   max_steps=max_steps, stream=stream,
                   git_protect=git_protect, checkpoint_mgr=checkpoint_mgr,
                   allow_downgrade=allow_downgrade, plan_mode=plan_mode,
                   compactor=compactor, anchor=anchor,
                   ext_registry=ext_registry, chat_state=chat_state,
                   compaction_policy=compaction_policy,
                   reminder_policy=reminder_policy,
                   strict=strict or False,
                   planner=planner,
                   perception_engine=perception_engine,
                   identity=identity)

    def get_judge(self, judge_type: str) -> Judge | None:
        """获取指定 judge_type 的 Judge 实例 (§12.1 多 Judge 编排)。

        优先查 judges dict, 若 judge_type 匹配则返回;
        否则 (judges dict 模式下) 返回 None -- 不隐式 fallback 到 judge 字段,
        保持多 Judge 模式的显式性。
        向后兼容: judges=None 且 judge 无 judge_type 属性时, 直接返回 judge
        (兼容未声明类型的旧 Judge, 保持旧行为)。
        对应 MASTER.md §12.1 Accountability 三态编排。
        """
        # 多 Judge 模式: 只查 judges dict, 不隐式 fallback 到 judge 字段
        if self.judges is not None:
            return self.judges.get(judge_type)
        # 向后兼容: 无 judges dict
        if self.judge is not None:
            # 若 judge 声明了 judge_type, 检查是否匹配
            if hasattr(self.judge, 'judge_type'):
                if self.judge.judge_type == judge_type:
                    return self.judge
                return None  # 类型不匹配
            # 旧 Judge 未声明类型 -> 直接返回 (保持旧行为)
            return self.judge
        return None
