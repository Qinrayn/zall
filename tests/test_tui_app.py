"""Tests for the TUI (terminal user interface) module.

Covers:
  - TuiApp importability and construction
  - Event handling (LoopEvent -> UI updates)
  - Message list management
  - Input bar behavior
  - Status bar updates
  - Fallback when textual is unavailable
  - Streaming token updates
  - CLI argument parsing for --tui/--no-tui

Design:
  - Uses textual's pilot testing mode when available
  - Uses mocks for textual when pilot is not suitable
  - Tests are structured to work both with and without textual installed
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
pytest.importorskip("textual")

from zall.core.loop_events import LoopEvent


# ── Helpers ──

def _make_event(kind: str, step: int = 1, **payload: object) -> LoopEvent:
    return LoopEvent(kind=kind, step=step, payload=dict(payload))


# ── Test 1: TuiApp importable ──

def test_tui_app_importable() -> None:
    """TuiApp and related components can be imported when textual is available."""
    from zall.cli.tui import TuiApp, run_tui, ChatMessage, MessageList, InputBar, StatusBar
    assert TuiApp is not None
    assert run_tui is not None
    assert ChatMessage is not None
    assert MessageList is not None
    assert InputBar is not None
    assert StatusBar is not None


# ── Test 2: TuiApp construction ──

def test_tui_app_construction() -> None:
    """TuiApp can be constructed with default parameters."""
    from zall.cli.tui import TuiApp
    app = TuiApp()
    assert app is not None
    assert app.TITLE == "zall"
    # v2.x: 未传 --model 时从 config 解析真实 model (修 'unset' 显示 bug)。
    # 环境无关: 无 config 时为 "", 有 config 时为配置的 model — 只验证不报错。
    assert isinstance(app._model, str)
    assert app._state.get("model") == app._model  # state 与显示一致


def test_tui_app_construction_with_params() -> None:
    """TuiApp can be constructed with model/yes/verbose/strict parameters."""
    from zall.cli.tui import TuiApp
    app = TuiApp(model="gpt-4", yes=True, verbose=True, strict=True)
    assert app._model == "gpt-4"
    assert app._yes is True
    assert app._verbose is True
    assert app._strict is True


# ── Test 3: ChatMessage model ──

class TestChatMessage:
    """ChatMessage model tests."""

    def test_user_message(self) -> None:
        from zall.cli.tui import ChatMessage
        msg = ChatMessage(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"
        rich = msg.to_rich()
        assert rich is not None

    def test_assistant_message(self) -> None:
        from zall.cli.tui import ChatMessage
        msg = ChatMessage(role="assistant", content="Hello! I'm an AI assistant.")
        assert msg.role == "assistant"
        rich = msg.to_rich()
        assert rich is not None

    def test_tool_message(self) -> None:
        from zall.cli.tui import ChatMessage
        msg = ChatMessage(role="tool", tool_id="bash", tool_args={"command": "ls"}, tool_success=True)
        assert msg.role == "tool"
        rich = msg.to_rich()
        assert rich is not None

    def test_thinking_message(self) -> None:
        from zall.cli.tui import ChatMessage
        msg = ChatMessage(role="thinking", thinking="reasoning step")
        assert msg.role == "thinking"
        rich = msg.to_rich()
        assert rich is not None

    def test_error_message(self) -> None:
        from zall.cli.tui import ChatMessage
        msg = ChatMessage(role="error", content="something went wrong")
        assert msg.role == "error"
        rich = msg.to_rich()
        assert rich is not None

    def test_streaming_message(self) -> None:
        from zall.cli.tui import ChatMessage
        msg = ChatMessage(role="assistant", streaming=True)
        msg._streaming_content = "partial token stream"
        assert msg.streaming is True
        rich = msg.to_rich()
        assert rich is not None


# ── Test 4: Event handling ──

class TestTuiAppEventHandling:
    """TuiApp event handling tests."""

    def test_handle_model_call_start(self) -> None:
        """TuiApp handles model_call_start event and updates model name."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        event = _make_event("model_call_start", model="gpt-4")
        app._handle_event(event)
        assert app.status_model == "gpt-4"
        assert app._is_streaming is True

    def test_handle_model_token(self) -> None:
        """TuiApp handles model_token event and accumulates streaming content."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        # Start streaming
        app._handle_event(_make_event("model_call_start"))
        # Add tokens
        app._handle_event(_make_event("model_token", token="Hello "))
        app._handle_event(_make_event("model_token", token="world!"))
        assert app._streaming_content == "Hello world!"

    def test_handle_model_call_with_content(self) -> None:
        """TuiApp handles model_call event with content and updates state."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        event = _make_event("model_call", content="Final response", usage={"total": 150})
        app._handle_event(event)
        assert app._is_streaming is False
        assert app.status_tokens == "150 tokens"

    def test_handle_tool_call_events(self) -> None:
        """TuiApp handles tool_call_start and tool_call_end events."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._handle_event(_make_event("tool_call_start", tool_id="bash", args={"command": "ls"}))
        app._handle_event(_make_event("tool_call_end", tool_id="bash", success=True, output="file1\nfile2\n"))
        # Should not crash
        assert app._tool_calls_in_step == 1

    def test_handle_error(self) -> None:
        """TuiApp handles error events."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._handle_event(_make_event("error", error="connection timeout"))
        # Should not crash
        assert app._is_streaming is False

    def test_handle_judge_result(self) -> None:
        """TuiApp handles judge_result events."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._handle_event(_make_event("judge_result", state="met", report="all tests passed"))
        # Should not crash

    def test_handle_step_progress(self) -> None:
        """TuiApp handles step_progress events."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._handle_event(_make_event("step_progress", message="analyzing code", phase="perception"))
        assert app._current_step == 1

    def test_handle_model_thinking(self) -> None:
        """TuiApp handles model_thinking events."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._handle_event(_make_event("model_thinking", token="reasoning step"))
        # Should not crash

    def test_handle_context_compaction(self) -> None:
        """TuiApp handles context_compaction events."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._handle_event(_make_event("context_compaction", compacted_count=5, reason="token limit"))
        # Should not crash

    def test_handle_override(self) -> None:
        """TuiApp handles override events."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._handle_event(_make_event("override", tool_id="bash"))
        # Should not crash

    def test_handle_gate_decision(self) -> None:
        """TuiApp handles gate_decision events."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._handle_event(_make_event("gate_decision", level="greylist", tool_id="bash"))
        app._handle_event(_make_event("gate_decision", level="blacklist", tool_id="rm"))
        # Should not crash

    def test_handle_tool_rejected(self) -> None:
        """TuiApp handles tool_rejected events."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._handle_event(_make_event("tool_rejected", tool_id="bash"))
        # Should not crash


# ── Test 5: Message list ──

class TestMessageList:
    """MessageList widget tests."""

    def test_add_user_message(self) -> None:
        """MessageList can add a user message."""
        from zall.cli.tui import MessageList, ChatMessage
        ml = MessageList()
        ml.add_message(ChatMessage(role="user", content="hello"))
        assert len(ml._messages) == 1
        assert ml._messages[0].role == "user"
        assert ml._messages[0].content == "hello"

    def test_add_assistant_message(self) -> None:
        """MessageList can add an assistant message."""
        from zall.cli.tui import MessageList, ChatMessage
        ml = MessageList()
        ml.add_message(ChatMessage(role="assistant", content="response"))
        assert len(ml._messages) == 1
        assert ml._messages[0].role == "assistant"

    def test_add_tool_message(self) -> None:
        """MessageList can add a tool message."""
        from zall.cli.tui import MessageList, ChatMessage
        ml = MessageList()
        ml.add_message(ChatMessage(role="tool", tool_id="bash", tool_success=True))
        assert len(ml._messages) == 1
        assert ml._messages[0].role == "tool"

    def test_messages_ordered(self) -> None:
        """Messages are added in order."""
        from zall.cli.tui import MessageList, ChatMessage
        ml = MessageList()
        ml.add_message(ChatMessage(role="user", content="first"))
        ml.add_message(ChatMessage(role="assistant", content="second"))
        ml.add_message(ChatMessage(role="user", content="third"))
        assert len(ml._messages) == 3
        assert ml._messages[0].content == "first"
        assert ml._messages[1].content == "second"
        assert ml._messages[2].content == "third"

    def test_last_message_accessor(self) -> None:
        """MessageList.last_message returns the most recent message."""
        from zall.cli.tui import MessageList, ChatMessage
        ml = MessageList()
        assert ml.last_message is None
        ml.add_message(ChatMessage(role="user", content="hello"))
        assert ml.last_message is not None
        assert ml.last_message.content == "hello"

    def test_update_last_message_streaming(self) -> None:
        """MessageList can update the last message's streaming content."""
        from zall.cli.tui import MessageList, ChatMessage
        ml = MessageList()
        ml.add_message(ChatMessage(role="assistant", streaming=True, content=""))
        ml.update_last_message("Hello world")
        assert ml.last_message is not None
        assert ml.last_message._streaming_content == "Hello world"


# ── Test 6: Input bar ──

class TestInputBar:
    """InputBar widget tests."""

    def test_input_bar_creation(self) -> None:
        """InputBar can be created."""
        from zall.cli.tui import InputBar
        bar = InputBar()
        assert bar is not None

    def test_input_bar_value(self) -> None:
        """InputBar value property works (without full app context)."""
        from zall.cli.tui import InputBar
        bar = InputBar()
        # Without a running app, we can't set the value via the property
        # (textual TextArea widget requires an active app).
        # Just verify the widget can be created and has the expected structure.
        assert bar is not None
        assert hasattr(bar, '_textarea')


# ── Test 7: Status bar ──

class TestStatusBar:
    """StatusBar widget tests."""

    def test_status_bar_reactive(self) -> None:
        """StatusBar reactive attributes work."""
        from zall.cli.tui import StatusBar
        bar = StatusBar()
        bar.model = "gpt-4"
        bar.step = 5
        bar.tokens = "1,500 tokens"
        bar.mode = "normal"
        assert bar.model == "gpt-4"
        assert bar.step == 5
        assert bar.tokens == "1,500 tokens"
        assert bar.mode == "normal"

    def test_status_bar_renders(self) -> None:
        """StatusBar.render() produces output."""
        from zall.cli.tui import StatusBar
        bar = StatusBar()
        result = bar.render()
        assert result is not None

    def test_status_bar_with_data(self) -> None:
        """StatusBar.render() with data produces output."""
        from zall.cli.tui import StatusBar
        bar = StatusBar()
        bar.model = "claude-4"
        bar.step = 3
        bar.tokens = "500 tokens"
        result = bar.render()
        assert result is not None


# ── Test 8: Fallback without textual ──

def test_fallback_without_textual() -> None:
    """When textual is not available, TUI mode falls back gracefully."""
    # Simulate textual not being available
    import builtins
    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == 'textual':
            raise ImportError("No module named 'textual'")
        return original_import(name, *args, **kwargs)

    with patch('builtins.__import__', side_effect=mock_import):
        # Re-import should not fail
        import importlib
        import zall.cli.tui
        importlib.reload(zall.cli.tui)


# ── Test 9: Streaming token updates ──

class TestStreaming:
    """Streaming token update tests."""

    def test_streaming_step_accumulation(self) -> None:
        """TuiApp accumulates streaming tokens across events."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._handle_event(_make_event("model_call_start"))
        tokens = ["Hello", " ", "world", "!"]
        for t in tokens:
            app._handle_event(_make_event("model_token", token=t))
        assert app._streaming_content == "Hello world!"

    def test_streaming_state_reset(self) -> None:
        """TuiApp resets streaming state on new model call."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._handle_event(_make_event("model_call_start"))
        app._handle_event(_make_event("model_token", token="first"))
        assert app._streaming_content == "first"
        # Second model call should reset
        app._handle_event(_make_event("model_call", content="final"))
        assert app._is_streaming is False


# ── Test 10: CLI argument parsing for TUI ──

class TestTuiCliArgs:
    """CLI 参数解析 (v2.x: 仅 --no-tui; --tui 全屏与 --inline 已删)。"""

    def test_no_tui_flag(self) -> None:
        """--no-tui flag sets tui_mode=False."""
        from zall.cli.app import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["--no-tui"])
        assert args.tui_mode is False

    def test_default_tui_mode_is_false(self) -> None:
        """v2.2 console 优先: 默认 tui_mode=False (console REPL; 仅 --tui 进 Textual)。"""
        from zall.cli.app import _build_parser
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.tui_mode is False
        # 显式 --tui 才转真; --no-tui 兼容别名回假
        assert parser.parse_args(["--tui"]).tui_mode is True
        assert parser.parse_args(["--no-tui"]).tui_mode is False

    def test_fullscreen_and_inline_flags_removed(self) -> None:
        """反例: 全屏/--inline 变体已删 (--tui 现为 inline 可选开关, 不再是全屏)。"""
        from zall.cli.app import _build_parser
        parser = _build_parser()
        for flag in ("-T", "--inline", "-I"):
            with pytest.raises(SystemExit):
                parser.parse_args([flag])


# ── Test 11: TuiApp exit behavior ──

def test_tui_app_exit_callback() -> None:
    """TuiApp calls the on_exit callback when exiting."""
    from zall.cli.tui import TuiApp
    app = TuiApp()
    callback = MagicMock()
    app.set_on_exit(callback)
    # Simulate exit
    with patch.object(app, 'exit'):
        app.action_exit()
        callback.assert_called_once()


# ── Test 12: TuiApp interrupt ──

def test_tui_app_interrupt() -> None:
    """TuiApp handles interrupt action."""
    from zall.cli.tui import TuiApp
    app = TuiApp()
    app._is_streaming = True
    app.action_interrupt()
    assert app._interrupt_requested is True


# ── Test 13: TuiApp clear screen ──

def test_tui_app_clear_screen() -> None:
    """TuiApp handles clear screen action."""
    from zall.cli.tui import TuiApp
    app = TuiApp()
    # Should not crash
    app.action_clear_screen()
    assert app._is_streaming is False


# ── Test 14: LoopEvent __call__ interface ──

def test_tui_app_call_interface() -> None:
    """TuiApp.__call__ accepts LoopEvent and queues it."""
    from zall.cli.tui import TuiApp
    app = TuiApp()
    event = _make_event("model_call_start", model="test-model")
    # Call the event (should be queued, not crash)
    app(event)
    assert len(app._event_queue) >= 1


# ── Test 15: TuiApp handle_loop_event interface ──

def test_tui_app_on_event_interface() -> None:
    """TuiApp.handle_loop_event accepts LoopEvent and delegates to __call__.

    Note: renamed from on_event to avoid clash with textual's App.on_event
    (lifecycle events like Mount/Unmount).
    """
    from zall.cli.tui import TuiApp
    app = TuiApp()
    event = _make_event("model_call_start", model="test-model")
    app.handle_loop_event(event)
    assert len(app._event_queue) >= 1


# ── Test 16: Event kind constants are handled ──

class TestEventKindHandling:
    """All known event kinds are handled without error."""

    EVENT_KINDS = [
        "model_call_start",
        "model_token",
        "model_thinking",
        "model_tool_call",
        "model_call",
        "tool_call_start",
        "tool_call_end",
        "tool_rejected",
        "gate_decision",
        "judge_result",
        "override",
        "step_progress",
        "context_compaction",
        "error",
        "runaway",
        "length_exceeded",
        "perception_state",
    ]

    def test_all_event_kinds_handled(self) -> None:
        """All known event kinds can be processed without error."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        for kind in self.EVENT_KINDS:
            app._handle_event(_make_event(kind, step=1, token="test",
                                          model="gpt-4", content="test",
                                          tool_id="bash", args={},
                                          success=True, output="test",
                                          error="test", usage={"total": 100},
                                          state="met", report="test",
                                          message="test", phase="test",
                                          compacted_count=5, reason="test",
                                          level="greylist",
                                          tool_calls=[{"tool_id": "bash", "args": {"command": "ls"}}],
                                          reasoning="test reasoning"))
        # Should not crash for any event kind


# ── Test 17: run_tui factory ──

def test_run_tui_importable() -> None:
    """run_tui factory function is importable."""
    from zall.cli.tui import run_tui
    assert callable(run_tui)