"""Tests for ChatState management system (Phase 2a).

IPR-0: invariant tests must be written before or alongside the code.
"""

from __future__ import annotations

import time
import pytest

from zall.core.chat_state import (
    ChatState,
    ChatStateHandle,
    CompactionResult,
    StateEventKind,
    StateMetadata,
    SummaryCompaction,
    UsageLedger,
)
from zall.core.model import Message, ToolCall


class TestUsageLedger:
    """UsageLedger invariants."""

    def test_empty_usage(self):
        ledger = UsageLedger()
        assert ledger.total_tokens == 0
        assert ledger.call_count == 0

    def test_record_usage(self):
        ledger = UsageLedger()
        ledger.record({"prompt": 100, "completion": 50}, model="gpt-4")
        assert ledger.total_prompt_tokens == 100
        assert ledger.total_completion_tokens == 50
        assert ledger.total_tokens == 150
        assert ledger.call_count == 1
        assert ledger.by_model["gpt-4"]["prompt"] == 100

    def test_record_multiple_calls(self):
        ledger = UsageLedger()
        ledger.record({"prompt": 100, "completion": 50})
        ledger.record({"prompt": 200, "completion": 100})
        assert ledger.total_prompt_tokens == 300
        assert ledger.total_completion_tokens == 150
        assert ledger.call_count == 2

    def test_reset(self):
        ledger = UsageLedger()
        ledger.record({"prompt": 100, "completion": 50})
        ledger.reset()
        assert ledger.total_tokens == 0
        assert ledger.call_count == 0


class TestChatState:
    """ChatState core invariants."""

    def test_empty_initial_state(self):
        state = ChatState()
        assert state.message_count == 0
        assert len(state.events) == 0
        assert state.usage.total_tokens == 0

    def test_initial_messages(self):
        msgs = [Message.user("hello")]
        state = ChatState(messages=msgs)
        assert state.message_count == 1
        assert state.messages[0].content == "hello"

    def test_push_user_message(self):
        state = ChatState()
        state.push_user_message("Hello, world!")
        assert state.message_count == 1
        assert state.messages[0].role == "user"
        assert state.messages[0].content == "Hello, world!"

    def test_push_assistant_response(self):
        state = ChatState()
        state.push_assistant_response("I can help!", tool_calls=(
            ToolCall(id="call_1", tool_id="read_file", args={"path": "foo.py"}),
        ))
        assert state.message_count == 1
        assert state.messages[0].role == "assistant"
        assert len(state.messages[0].tool_calls) == 1

    def test_push_tool_result(self):
        state = ChatState()
        state.push_tool_result("call_1", "file content", tool_id="read_file")
        assert state.message_count == 1
        assert state.messages[0].role == "tool"
        assert state.messages[0].tool_call_id == "call_1"

    def test_events_recorded_for_mutations(self):
        state = ChatState()
        state.push_user_message("hello")
        assert len(state.events) == 1
        assert state.events[0].kind == StateEventKind.USER_MESSAGE

        state.push_assistant_response("reply")
        assert len(state.events) == 2
        assert state.events[1].kind == StateEventKind.ASSISTANT_RESPONSE

        state.push_tool_result("c1", "output")
        assert len(state.events) == 3
        assert state.events[2].kind == StateEventKind.TOOL_RESULT

    def test_messages_immutable_snapshot(self):
        state = ChatState()
        state.push_user_message("hello")
        snapshot = state.messages
        assert len(snapshot) == 1
        # Modifying snapshot should not affect state
        snapshot.clear()
        assert state.message_count == 1

    def test_events_immutable_snapshot(self):
        state = ChatState()
        state.push_user_message("hello")
        events = state.events
        assert len(events) == 1
        events.clear()
        assert len(state.events) == 1

    def test_remove_by_predicate(self):
        state = ChatState()
        state.push_user_message("keep me")
        state.push_user_message("remove me")
        state.push_user_message("keep me too")

        removed = state.remove_by_predicate(
            lambda m: m.content == "remove me",
        )
        assert removed == 1
        assert state.message_count == 2
        assert state.messages[0].content == "keep me"
        assert state.messages[1].content == "keep me too"

    def test_replace_messages(self):
        state = ChatState()
        state.push_user_message("old")
        from zall.core.model import Message
        new_msgs = [Message.user("new1"), Message.user("new2")]
        state.replace_messages(new_msgs)
        assert state.message_count == 2
        assert state.messages[0].content == "new1"

    def test_reset_clears_messages(self):
        state = ChatState()
        state.push_user_message("hello")
        state.reset()
        assert state.message_count == 0

    def test_events_preserved_after_reset(self):
        state = ChatState()
        state.push_user_message("hello")
        state.reset()
        # Events should still be there (audit trail)
        assert len(state.events) >= 1

    def test_metadata_auto_updated(self):
        state = ChatState()
        t0 = state.metadata.created_at
        assert t0 > 0
        time.sleep(0.01)
        state.push_user_message("hello")
        assert state.metadata.updated_at > t0

    def test_snapshot_roundtrip(self):
        state = ChatState()
        state.push_user_message("hello")
        state.push_assistant_response("world")
        state.record_usage({"prompt": 100, "completion": 50})

        snapshot = state.snapshot()
        assert len(snapshot.messages) == 2
        assert snapshot.usage["prompt"] == 100

        # Restore into new state
        state2 = ChatState()
        state2.restore(snapshot)
        assert state2.message_count == 2
        assert state2.usage.total_prompt_tokens == 100
        assert state2.messages[0].content == "hello"

    def test_estimate_tokens(self):
        state = ChatState()
        state.push_user_message("hello world")
        estimated = state.estimate_tokens()
        assert estimated > 0


class TestSummaryCompaction:
    """SummaryCompaction invariants."""

    def test_no_compaction_when_under_limit(self):
        state = ChatState()
        for i in range(5):
            state.push_user_message(f"message {i}")
        state.push_assistant_response("reply")

        strategy = SummaryCompaction(keep_last=10)
        result = strategy.compact(state.messages, state.events)
        assert result.compacted_count == 0
        assert len(result.compacted_messages) == 6

    def test_compaction_when_over_limit(self):
        state = ChatState()
        for i in range(20):
            state.push_user_message(f"message {i}")

        strategy = SummaryCompaction(keep_last=5)
        result = strategy.compact(state.messages, state.events)
        assert result.compacted_count == 15
        assert len(result.compacted_messages) == 5

    def test_chat_state_compact(self):
        state = ChatState()
        state.push_user_message("first")
        for i in range(20):
            state.push_user_message(f"message {i}")

        # Inject compact strategy
        from zall.core.chat_state import SummaryCompaction
        state._compaction_strategy = SummaryCompaction(keep_last=5)

        result = state.compact()
        assert result.compacted_count > 0
        assert state.compaction_count == 1

    def test_default_strategy(self):
        state = ChatState()
        for i in range(10):
            state.push_user_message(f"msg {i}")
        result = state.compact()
        assert result.compacted_count == 0  # 10 < 10 keep_last


class TestChatStateHandle:
    """ChatStateHandle invariants."""

    def test_handle_delegation(self):
        state = ChatState()
        handle = state.handle()
        assert isinstance(handle, ChatStateHandle)

        handle.push_user_message("via handle")
        assert state.message_count == 1

    def test_handle_queries(self):
        state = ChatState()
        handle = ChatStateHandle(state)
        handle.push_user_message("hello")
        assert len(handle.messages) == 1
        assert handle.message_count == 1

    def test_handle_compact(self):
        state = ChatState()
        handle = state.handle()
        for i in range(20):
            handle.push_user_message(f"msg {i}")
        state._compaction_strategy = SummaryCompaction(keep_last=5)
        result = handle.compact()
        assert result.compacted_count == 15

    def test_handle_snapshot(self):
        state = ChatState()
        handle = state.handle()
        handle.push_user_message("hello")
        snap = handle.snapshot()
        assert len(snap.messages) == 1
        assert snap.messages[0].content == "hello"

    def test_handle_metadata(self):
        state = ChatState()
        handle = state.handle()
        assert handle.metadata.prompt_index == 0
        handle.increment_prompt_index()
        assert handle.prompt_index == 1


class TestMessageIntegration:
    """Integration: ChatState + Message types."""

    def test_full_conversation_flow(self):
        state = ChatState()

        # User says something
        state.push_user_message("Read foo.py and tell me what it does")

        # Assistant responds with tool calls
        state.push_assistant_response(
            "",
            tool_calls=(
                ToolCall(id="call_1", tool_id="read_file", args={"path": "foo.py"}),
            ),
        )

        # Tool result
        state.push_tool_result("call_1", "def hello(): pass", tool_id="read_file")

        # Assistant final response
        state.push_assistant_response(
            "The file defines a hello() function.",
        )

        assert state.message_count == 4
        assert state.messages[0].role == "user"
        assert state.messages[1].role == "assistant"
        assert len(state.messages[1].tool_calls) == 1
        assert state.messages[2].role == "tool"
        assert state.messages[3].role == "assistant"
        assert len(state.events) == 4

    def test_messages_pydantic_frozen(self):
        msg = Message.user("hello")
        assert msg.role == "user"
        with pytest.raises(Exception):
            msg.role = "assistant"


class TestStateEvent:
    """StateEvent invariants."""

    def test_event_kind(self):
        state = ChatState()
        state.push_user_message("test")
        ev = state.events[0]
        assert ev.kind == StateEventKind.USER_MESSAGE
        assert ev.timestamp > 0
        assert "content_preview" in ev.payload

    def test_event_age(self):
        state = ChatState()
        state.push_user_message("test")
        ev = state.events[0]
        age = ev.age_seconds
        assert age >= 0


class TestMetadata:
    """StateMetadata invariants."""

    def test_metadata_defaults(self):
        meta = StateMetadata()
        assert meta.prompt_index == 0
        assert meta.turn_count == 0
        assert meta.compaction_count == 0
        assert meta.created_at > 0

    def test_prompt_index_increment(self):
        state = ChatState()
        assert state.prompt_index == 0
        state.increment_prompt_index()
        assert state.prompt_index == 1