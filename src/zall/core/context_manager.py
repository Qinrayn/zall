"""zall.core.context_manager — Context window management (watermark + compaction).

Extracted from AgentLoop to reduce loop.py's ~1362 lines into focused collaborators.

Responsibilities:
  - Watermark monitoring: checks context window usage before model calls
  - Reactive compaction: triggers auto-compact on LENGTH or high watermark
  - Nudge injection: handles empty STOP replies

Inspired by Grok Build's xai-chat-state compaction mode, but adapted to
zall's synchronous, model-agnostic design. No actor pattern needed here —
AgentLoop is already the single-threaded coordinator.

Usage:
    mgr = ContextManager(loop, compactor)
    mgr.check_watermark_before_call(messages, model_name, step)
    mgr.handle_length(resp)

IPR constraints:
  IPR-3: pydantic / stdlib only, no model SDK
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from zall.core.model import Message, StopReason
from zall.core.verifiability import EventType

if TYPE_CHECKING:
    from zall.core.compactor import Compactor
    from zall.core.loop import AgentLoop


from zall.core.prompt_template import render as _render_template


def _loop_event(*args: Any, **kwargs: Any) -> Any:
    """Lazy import LoopEvent to avoid circular import with loop.py."""
    from zall.core.loop_events import LoopEvent
    return LoopEvent(*args, **kwargs)


def _empty_stop_nudge() -> str:
    """Return the empty-stop nudge text via template system."""
    return _render_template("empty_stop_nudge")


class ContextManager:
    """Context window management for AgentLoop.

    Encapsulates:
      - Watermark monitoring (preemptive compaction before LENGTH)
      - Reactive compaction (on LENGTH stop_reason)
      - Empty STOP nudge injection
      - Mid-turn interjection buffer (v0.5.0, Grok Build 启发)

    Thread-safety: thread-safe for interjection buffer (locked),
    single-threaded for the rest.
    """

    def __init__(self, loop: AgentLoop, compactor: Compactor | None = None) -> None:
        self._loop = loop
        self._compactor = compactor
        # Watermark monitor reference (cached from compactor)
        self._wm: Any | None = (
            getattr(self._compactor, "watermark_monitor", None)
            if self._compactor is not None else None
        )
        # Watermark check counter: check every 3 steps once context grows.
        self._check_counter: int = 0
        # Compaction attempt counter (for event_id uniqueness)
        self._compaction_count: int = 0

        # v0.5.0: Mid-turn interjection buffer (thread-safe)
        self._interjection_buffer: list[str] = []
        self._interjection_lock = threading.Lock()

    # ── Public API ──

    @property
    def compactor(self) -> Compactor | None:
        return self._compactor

    @property
    def compaction_count(self) -> int:
        return self._compaction_count

    def reset_check_counter(self) -> None:
        """Reset watermark check counter (called at run start)."""
        self._check_counter = 0

    def mark_dirty(self) -> None:
        """Mark watermark cache as dirty when messages change."""
        if self._wm is not None and hasattr(self._wm, "mark_dirty"):
            self._wm.mark_dirty()

    # ── Mid-turn interjection buffer (v0.5.0, Grok Build 启发) ──

    def push_interjection(self, text: str) -> None:
        """Push a user interjection message (thread-safe).

        Called from CLI/repl thread when user sends a message
        while the agent is executing a step.
        """
        with self._interjection_lock:
            self._interjection_buffer.append(text)

    def drain_interjections(self) -> list[str]:
        """Drain all pending interjections (thread-safe).

        Returns list of interjection texts, or empty list if none.
        Called at the start of each step to inject user messages.
        """
        with self._interjection_lock:
            result = list(self._interjection_buffer)
            self._interjection_buffer.clear()
        return result

    @property
    def has_interjections(self) -> bool:
        """Check if there are pending interjections (thread-safe)."""
        with self._interjection_lock:
            return len(self._interjection_buffer) > 0

    def check_watermark_before_call(
        self, messages: list[Message], model_name: str, step_count: int,
        real_tokens: int | None = None,
    ) -> None:
        """§9.2.9 Preemptive watermark check before model call.

        v0.1.0: watermark > 75% suggests compaction, > 90% forces compaction.
        O2: small context (< 20 messages) skips check.
        O4: checks every 3 steps to reduce overhead.
        v2 fix: first check triggers immediately at 20 messages (no gate).
        PARADIGM Step 0: real_tokens (上次响应真实 prompt tokens) 传入水位判定。
        """
        self._check_counter += 1
        should_check = (
            self._compactor is not None
            and self._wm is not None
            and len(messages) >= 20
            and (self._check_counter <= 1
                 or self._check_counter % 3 == 0)
        )
        if not should_check:
            return

        assert self._wm is not None
        watermark_action = self._wm.check_watermark(
            messages, model_name, step_count, real_tokens=real_tokens,
        )
        if watermark_action == "force":
            if self._auto_compact(reason="watermark_force"):
                self._wm.record_compaction(step_count)
        elif watermark_action == "suggest":
            if self._auto_compact(reason="watermark_suggest"):
                self._wm.record_compaction(step_count)

    def handle_empty_stop(
        self, messages: list[Message], model_call_count: int, step_count: int,
    ) -> bool:
        """Handle empty STOP reply by injecting a nudge.

        v0.4.9: Uses loop.append_message() to ensure ChatState is synchronized.
        v0.4.10: Cache nudge text to avoid redundant lazy import.

        Returns True if nudge was injected (caller should retry model call).
        Returns False if no nudge needed (normal STOP handling).
        """
        nudge = _empty_stop_nudge()
        # Use loop.append_message to keep ChatState in sync
        self._loop.append_message(Message.assistant(content=""))
        self._loop.append_message(Message(role="system", content=nudge))
        # Record nudge in timeline (§6.1 fidelity)
        self._loop.recorder.append(
            event_id=f"nudge_{step_count}_{model_call_count}",
            ts=int(time.time() * 1000),
            event_type=EventType.SYSTEM_INJECTION,
            payload={"reason": "empty_stop", "nudge": nudge[:200]},
        )
        return True

    def is_empty_stop(self, resp: Any) -> bool:
        """Check if response is an empty STOP (no content, no tool calls)."""
        return (resp.stop_reason == StopReason.STOP
                and not (resp.content or "").strip())

# ── Internal ──

    def _auto_compact(self, *, reason: str) -> bool:
        """Internal: compact messages, apply result to loop, emit events.

        v0.4.10: Removed redundant `messages` parameter — always operates
        on self._loop._messages directly, eliminating the fragile invariant
        where the caller's list reference had to match self._loop._messages.

        v0.5.0 (B3 fix): Record timeline event BEFORE applying compressed
        messages. If timeline recording fails, messages are not corrupted.
        Previously messages were mutated before recording, violating the
        invariant "timeline is the source of truth".

        Returns True if compaction actually happened.
        """
        if self._compactor is None:
            return False

        try:
            result = self._compactor.compact(self._loop.messages, self._loop.model_adapter)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            self._loop._emit(_loop_event(
                kind="error",
                step=self._loop.step_count,
                payload={"error": f"compaction failed: {e}", "type": type(e).__name__},
            ))
            return False

        if result.compacted_count <= 0:
            return False

        self._compaction_count += 1

        # ★ Record CONTEXT_COMPACTION in timeline FIRST (§6.1 fidelity)
        #    Do this BEFORE mutating messages to preserve the invariant
        #    that timeline is the source of truth.
        self._loop.recorder.append(
            event_id=f"compact_{self._compaction_count}",
            ts=int(time.time() * 1000),
            event_type=EventType.CONTEXT_COMPACTION,
            payload={
                "step": self._loop.step_count,
                "reason": reason,
                "compacted_count": result.compacted_count,
                "strategy": result.strategy,
                "summary_preview": result.summary[:200] if result.summary else "",
            },
        )

        # ★ 通过 AgentLoop.set_messages() 替换消息 (v0.5.1: ChatState 是唯一来源)
        self._loop.set_messages(list(result.compressed_messages))

        # Emit observer event (non-critical, after mutation is safe)
        self._loop._emit(_loop_event(
            kind="context_compaction",
            step=self._loop.step_count,
            payload={
                "reason": reason,
                "compacted_count": result.compacted_count,
                "strategy": result.strategy,
            },
        ))
        return True