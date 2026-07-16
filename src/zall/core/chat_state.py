"""zall.core.chat_state — Actor-based chat state management.

Inspired by Grok Build's xai-chat-state crate. Manages conversation state
(message history, token usage, compaction events) through a clean actor
pattern with typed commands, queries, events, and pluggable persistence.

Architecture:
  ┌────────────────┐                  ┌──────────────────────────────┐
  │ AgentLoop      │ ─── Command ───▶ │       ChatState              │
  │  (consumer)    │                  │  (owns all conversation)     │
  │                │                  │                              │
  │  ChatStateHandle──────────────────│  State (single-threaded):     │
  │  (send cmd)    │                  │  - messages: list[Message]   │
  │                │                  │  - events: list[StateEvent]  │
  └────────────────┘                  │  - token_usage: UsageLedger  │
                                      │  - metadata: StateMetadata   │
                                      └──────────────────────────────┘

Usage:
    state = ChatState(messages=initial_msgs)
    handle = state.handle()
    handle.push_user_message(content)
    handle.push_assistant_response(resp)
    handle.push_tool_result(tool_call_id, content)
    snapshot = handle.snapshot()

IPR constraints:
  IPR-0: invariant tests at tests/test_chat_state_invariants.py
  IPR-3: pydantic / stdlib only, no model SDK
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol


# ═══════════════════════════════════════════════════════════════════
# §1  Event System
# ═══════════════════════════════════════════════════════════════════


class StateEventKind(str, Enum):
    """ChatState 事件类型 — 状态变化的记录。"""
    USER_MESSAGE = "user_message"
    ASSISTANT_RESPONSE = "assistant_response"
    TOOL_RESULT = "tool_result"
    COMPACTION = "compaction"
    SYSTEM_INJECTION = "system_injection"
    RESET = "reset"
    REPLACE = "replace"
    METADATA_CHANGED = "metadata_changed"


@dataclass(frozen=True)
class StateEvent:
    """ChatState 中的一条事件记录。

    每个 mutation 操作都会产生一条不可变事件。
    用于审计、回放、压缩策略分析。
    """
    kind: StateEventKind
    timestamp: float  # epoch seconds
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp


# ═══════════════════════════════════════════════════════════════════
# §2  Usage Tracking
# ═══════════════════════════════════════════════════════════════════


@dataclass
class UsageLedger:
    """Token 用量分类账。

    记录每次 model call 的 token 消耗，
    支持按类型 (prompt/completion) 和模型汇总。
    """
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cached_tokens: int = 0
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    call_count: int = 0

    def record(self, usage: dict[str, int], model: str = "") -> None:
        """记录一次 model call 的用量。

        Args:
            usage: {"prompt": int, "completion": int, "cached": int}
            model: 模型名称
        """
        prompt = int(usage.get("prompt", 0) or 0)
        completion = int(usage.get("completion", 0) or 0)
        cached = int(usage.get("cached", 0) or 0)
        self.total_prompt_tokens += prompt
        self.total_completion_tokens += completion
        self.total_cached_tokens += cached
        self.call_count += 1
        if model:
            by_model = self.by_model.setdefault(model, {"prompt": 0, "completion": 0, "cached": 0, "calls": 0})
            by_model["prompt"] += prompt
            by_model["completion"] += completion
            by_model["cached"] += cached
            by_model["calls"] = by_model.get("calls", 0) + 1

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    def reset(self) -> None:
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cached_tokens = 0
        self.by_model.clear()
        self.call_count = 0


# ═══════════════════════════════════════════════════════════════════
# §3  State Metadata
# ═══════════════════════════════════════════════════════════════════


@dataclass
class StateMetadata:
    """ChatState 元数据 — 不参与消息内容但影响状态管理。"""
    prompt_index: int = 0
    """用户 prompt 序号 (每轮用户输入递增)"""
    turn_count: int = 0
    """总轮次"""
    compaction_count: int = 0
    """压缩次数"""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    tags: dict[str, str] = field(default_factory=dict)
    """自定义标签 (如 agent_name, session_id)"""


# ═══════════════════════════════════════════════════════════════════
# §4  Compaction Strategy
# ═══════════════════════════════════════════════════════════════════


@dataclass
class CompactionResult:
    """压缩结果。"""
    compacted_messages: list[Any]
    compacted_count: int
    strategy: str = "summary"
    summary: str = ""


class CompactionStrategy(Protocol):
    """压缩策略协议 — 决定如何压缩历史消息。"""
    def compact(
        self,
        messages: list[Any],
        events: list[StateEvent],
    ) -> CompactionResult: ...


class SummaryCompaction:
    """摘要压缩 — 将早期消息替换为摘要。"""
    def __init__(self, keep_last: int = 10) -> None:
        self._keep_last = keep_last

    def compact(
        self,
        messages: list[Any],
        events: list[StateEvent],  # noqa: ARG002
    ) -> CompactionResult:
        if len(messages) <= self._keep_last:
            return CompactionResult(
                compacted_messages=list(messages),
                compacted_count=0,
                strategy="summary",
                summary="",
            )
        keep = messages[-self._keep_last:]
        compacted = len(messages) - self._keep_last
        return CompactionResult(
            compacted_messages=keep,
            compacted_count=compacted,
            strategy="summary",
            summary=f"[{compacted} earlier messages compacted]",
        )


# ═══════════════════════════════════════════════════════════════════
# §5  Persistence
# ═══════════════════════════════════════════════════════════════════


@dataclass
class Snapshot:
    """ChatState 的完整快照 — 用于保存/恢复/回放。"""
    messages: list[Any] = field(default_factory=list)
    events: list[StateEvent] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class ChatPersistence(Protocol):
    """可选持久化后端协议。"""
    def save(self, snapshot: Snapshot) -> None: ...
    def load(self) -> Snapshot | None: ...


class NullPersistence:
    """空持久化 — 不保存任何内容。"""
    def save(self, snapshot: Snapshot) -> None:  # noqa: ARG002
        pass

    def load(self) -> Snapshot | None:
        return None


# ═══════════════════════════════════════════════════════════════════
# §6  ChatState — 核心状态管理
# ═══════════════════════════════════════════════════════════════════


class ChatState:
    """对话状态管理器 — Actor 模式封装。

    所有 mutation 通过方法来操作, 并自动记录事件。
    支持快照、压缩、用量追踪。

    线程安全: 单线程使用即可 (调用方负责同步)。
    """

    def __init__(
        self,
        messages: list[Any] | None = None,
        usage: UsageLedger | None = None,
        metadata: StateMetadata | None = None,
        persistence: ChatPersistence | None = None,
        compaction_strategy: CompactionStrategy | None = None,
    ) -> None:
        self._messages: list[Any] = list(messages) if messages else []
        self._events: list[StateEvent] = []
        self._usage = usage or UsageLedger()
        self._metadata = metadata or StateMetadata()
        self._persistence = persistence or NullPersistence()
        self._compaction_strategy = compaction_strategy or SummaryCompaction(keep_last=10)

    # ── Events ──

    def _record_event(self, kind: StateEventKind, **payload: Any) -> None:
        """记录一条不可变事件。"""
        self._events.append(StateEvent(
            kind=kind,
            timestamp=time.time(),
            payload=payload,
        ))
        self._metadata.updated_at = time.time()

    @property
    def events(self) -> list[StateEvent]:
        """所有事件 (只读快照)。"""
        return list(self._events)

    # ── Messages ──

    @property
    def messages(self) -> list[Any]:
        """当前消息列表 (只读快照)。"""
        return list(self._messages)

    @messages.setter
    def messages(self, value: list[Any]) -> None:
        """替换整个消息列表 (供 AgentLoop 兼容)。"""
        self._messages = list(value)

    @property
    def message_count(self) -> int:
        return len(self._messages)

    def push_user_message(self, content: str) -> None:
        """添加用户消息。"""
        from zall.core.model import Message
        self._messages.append(Message.user(content))
        self._record_event(
            StateEventKind.USER_MESSAGE,
            content_preview=content[:100],
        )

    def push_assistant_response(
        self,
        content: str,
        tool_calls: tuple[Any, ...] = (),
    ) -> None:
        """添加 assistant 回复。"""
        from zall.core.model import Message
        self._messages.append(
            Message.assistant(content=content, tool_calls=tool_calls)
        )
        self._record_event(
            StateEventKind.ASSISTANT_RESPONSE,
            content_preview=content[:100],
            tool_call_count=len(tool_calls),
        )

    def push_tool_result(
        self,
        tool_call_id: str,
        content: str,
        tool_id: str = "",
    ) -> None:
        """添加工具结果回灌。"""
        from zall.core.model import Message
        self._messages.append(
            Message.tool_result(
                tool_call_id=tool_call_id,
                content=content,
                tool_id=tool_id,
            )
        )
        self._record_event(
            StateEventKind.TOOL_RESULT,
            tool_call_id=tool_call_id,
            tool_id=tool_id,
            content_preview=content[:100],
        )

    def push_system_message(self, content: str) -> None:
        """添加系统消息 (用于 nudge, 注入等)。"""
        from zall.core.model import Message
        self._messages.append(Message(role="system", content=content))
        self._record_event(
            StateEventKind.SYSTEM_INJECTION,
            content_preview=content[:100],
        )

    def replace_messages(self, new_messages: list[Any]) -> None:
        """替换消息列表 (用于压缩/回退)。"""
        old_count = len(self._messages)
        self._messages = list(new_messages)
        self._record_event(
            StateEventKind.REPLACE,
            old_count=old_count,
            new_count=len(new_messages),
        )

    def remove_by_predicate(self, predicate: Callable[[Any], bool]) -> int:
        """按谓词删除消息。"""
        before = len(self._messages)
        self._messages = [m for m in self._messages if not predicate(m)]
        removed = before - len(self._messages)
        if removed > 0:
            self._record_event(
                StateEventKind.RESET,
                removed_count=removed,
            )
        return removed

    def reset(self) -> None:
        """清空所有消息 (保留元数据)。"""
        self._messages.clear()
        self._record_event(StateEventKind.RESET, removed_count=-1)

    # ── Usage ──

    @property
    def usage(self) -> UsageLedger:
        return self._usage

    def record_usage(self, usage_dict: dict[str, int], model: str = "") -> None:
        self._usage.record(usage_dict, model=model)

    # ── Compaction ──

    def compact(self) -> CompactionResult:
        """压缩历史消息。"""
        result = self._compaction_strategy.compact(self._messages, self._events)
        if result.compacted_count > 0:
            self._messages = list(result.compacted_messages)
            self._metadata.compaction_count += 1
            self._record_event(
                StateEventKind.COMPACTION,
                compacted_count=result.compacted_count,
                strategy=result.strategy,
            )
        return result

    @property
    def compaction_count(self) -> int:
        return self._metadata.compaction_count

    # ── Metadata ──

    @property
    def metadata(self) -> StateMetadata:
        return self._metadata

    @property
    def prompt_index(self) -> int:
        return self._metadata.prompt_index

    def increment_prompt_index(self) -> None:
        self._metadata.prompt_index += 1
        self._metadata.turn_count += 1

    # ── Snapshot / Persistence ──

    def snapshot(self) -> Snapshot:
        """创建当前状态的快照。"""
        return Snapshot(
            messages=list(self._messages),
            events=list(self._events),
            usage={
                "prompt": self._usage.total_prompt_tokens,
                "completion": self._usage.total_completion_tokens,
                "total": self._usage.total_tokens,
                "calls": self._usage.call_count,
                "by_model": dict(self._usage.by_model),
            },
            metadata={
                "prompt_index": self._metadata.prompt_index,
                "turn_count": self._metadata.turn_count,
                "compaction_count": self._metadata.compaction_count,
                "created_at": self._metadata.created_at,
                "updated_at": self._metadata.updated_at,
                "tags": dict(self._metadata.tags),
            },
        )

    def restore(self, snapshot: Snapshot) -> None:
        """从快照恢复状态。"""
        self._messages = list(snapshot.messages)
        self._events = list(snapshot.events)
        if snapshot.usage:
            usage = snapshot.usage
            ledger = UsageLedger()
            ledger.total_prompt_tokens = usage.get("prompt", 0)
            ledger.total_completion_tokens = usage.get("completion", 0)
            ledger.call_count = usage.get("calls", 0)
            ledger.by_model = dict(usage.get("by_model", {}))
            self._usage = ledger
        if snapshot.metadata:
            meta = snapshot.metadata
            self._metadata.prompt_index = meta.get("prompt_index", 0)
            self._metadata.turn_count = meta.get("turn_count", 0)
            self._metadata.compaction_count = meta.get("compaction_count", 0)
            self._metadata.tags = dict(meta.get("tags", {}))

    def save(self) -> None:
        """持久化当前状态。"""
        self._persistence.save(self.snapshot())

    def load(self) -> bool:
        """从持久化恢复状态。返回 True 如果成功加载。"""
        snapshot = self._persistence.load()
        if snapshot is not None:
            self.restore(snapshot)
            return True
        return False

    # ── Convenience ──

    def handle(self) -> ChatStateHandle:
        """创建操作句柄。"""
        return ChatStateHandle(self)

    def estimate_tokens(self) -> int:
        """估算当前消息的 token 数量 (字符数/4 的粗略估计)。"""
        total = 0
        from zall.core.model import Message
        for msg in self._messages:
            if isinstance(msg, Message):
                total += len(msg.content) + 50  # 50 tokens overhead per message
                if msg.tool_calls:
                    total += sum(len(str(tc.args)) for tc in msg.tool_calls)
        return total

    def __len__(self) -> int:
        return len(self._messages)

    def __repr__(self) -> str:
        return (
            f"ChatState(messages={len(self._messages)}, "
            f"events={len(self._events)}, "
            f"tokens={self._usage.total_tokens})"
        )


# ═══════════════════════════════════════════════════════════════════
# §7  ChatStateHandle — 操作句柄
# ═══════════════════════════════════════════════════════════════════


class ChatStateHandle:
    """ChatState 的操作句柄 — 提供便捷的 Mutation/Query API。

    所有操作直接委托给 ChatState 实例。
    与 Grok Build 的 ChatStateHandle 概念对应。
    """

    def __init__(self, state: ChatState) -> None:
        self._state = state

    # ── Mutations ──

    def push_user_message(self, content: str) -> None:
        self._state.push_user_message(content)

    def push_assistant_response(
        self,
        content: str,
        tool_calls: tuple[Any, ...] = (),
    ) -> None:
        self._state.push_assistant_response(content, tool_calls=tool_calls)

    def push_tool_result(
        self,
        tool_call_id: str,
        content: str,
        tool_id: str = "",
    ) -> None:
        self._state.push_tool_result(tool_call_id, content, tool_id=tool_id)

    def push_system_message(self, content: str) -> None:
        self._state.push_system_message(content)

    def replace_messages(self, new_messages: list[Any]) -> None:
        self._state.replace_messages(new_messages)

    def remove_by_predicate(self, predicate: Callable[[Any], bool]) -> int:
        return self._state.remove_by_predicate(predicate)

    def record_usage(self, usage: dict[str, int], model: str = "") -> None:
        self._state.record_usage(usage, model=model)

    def compact(self) -> CompactionResult:
        return self._state.compact()

    def reset(self) -> None:
        self._state.reset()

    # ── Queries ──

    @property
    def messages(self) -> list[Any]:
        return self._state.messages

    @property
    def message_count(self) -> int:
        return self._state.message_count

    @property
    def events(self) -> list[StateEvent]:
        return self._state.events

    @property
    def usage(self) -> UsageLedger:
        return self._state.usage

    @property
    def metadata(self) -> StateMetadata:
        return self._state.metadata

    @property
    def compaction_count(self) -> int:
        return self._state.compaction_count

    @property
    def prompt_index(self) -> int:
        return self._state.prompt_index

    def increment_prompt_index(self) -> None:
        self._state.increment_prompt_index()

    def estimate_tokens(self) -> int:
        return self._state.estimate_tokens()

    def snapshot(self) -> Snapshot:
        return self._state.snapshot()

    def save(self) -> None:
        self._state.save()

    def load(self) -> bool:
        return self._state.load()

    # ── Legacy API: 消息列表直接操作 (供 AgentLoop 兼容) ──

    @property
    def raw_messages(self) -> list[Any]:
        """直接访问内部消息列表 (仅供 AgentLoop 迁移用)。"""
        return self._state._messages