"""zall.core.loop_config — AgentConfig + _GitProtectProtocol."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable
from zall.core.checkpoint import CheckpointManager
from zall.core.compactor import Compactor
from zall.core.events import EventBus
from zall.core.extension import ExtensionRegistry
from zall.core.accountability import Judge
from zall.core.chat_state import ChatState
from zall.core.verifiability import TrustAnchor
from zall.core.policies import CompactionPolicy, ReminderPolicy

@runtime_checkable
class _GitProtectProtocol(Protocol):
    def is_git_repo(self) -> bool: ...
    def checkpoint(self, label: str = "") -> dict[str, Any] | None: ...
    def rollback(self, to_index: int | None = None) -> bool: ...

@dataclass(frozen=True)
class AgentConfig:
    judge: Judge | None = None
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

    @classmethod
    def from_kwargs(cls, judge=None, observer=None, event_bus=None,
                    max_steps=None, stream=None, git_protect=None,
                    checkpoint_mgr=None, allow_downgrade=None, plan_mode=None,
                    compactor=None, anchor=None, ext_registry=None,
                    chat_state=None, compaction_policy=None,
                    reminder_policy=None) -> AgentConfig:
        return cls(judge=judge, observer=observer, event_bus=event_bus,
                   max_steps=max_steps, stream=stream,
                   git_protect=git_protect, checkpoint_mgr=checkpoint_mgr,
                   allow_downgrade=allow_downgrade, plan_mode=plan_mode,
                   compactor=compactor, anchor=anchor,
                   ext_registry=ext_registry, chat_state=chat_state,
                   compaction_policy=compaction_policy,
                   reminder_policy=reminder_policy)
