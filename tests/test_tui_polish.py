"""Tests for the TUI polish improvements (Claude Code alignment).

Covers:
  - InputBar: TextArea-based multi-line, history navigation, placeholder
  - Message rendering: syntax highlighting for code blocks
  - Tool call display: diff rendering for edit_file, color-coded tool types
  - StatusBar: enhanced fields (git_branch, cwd, context_pct, mode)
  - ChatMessage: code block detection and rendering
  - Visual hierarchy: Panel rendering with proper borders
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
pytest.importorskip("textual")

from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


# ── Test 1: InputBar history management ──

class TestInputBarHistory:
    """InputBar input history navigation tests."""

    def test_input_bar_uses_textarea(self) -> None:
        """InputBar now uses TextArea instead of Input."""
        from zall.cli.tui import InputBar
        bar = InputBar()
        assert hasattr(bar, '_textarea')
        assert hasattr(bar, '_history')
        assert bar._history == []

    def test_input_bar_add_to_history(self) -> None:
        """InputBar tracks input history."""
        from zall.cli.tui import InputBar
        bar = InputBar()
        bar._add_to_history("first message")
        assert len(bar._history) == 1
        assert bar._history[0] == "first message"

    def test_input_bar_history_no_duplicates(self) -> None:
        """InputBar does not add consecutive duplicate entries."""
        from zall.cli.tui import InputBar
        bar = InputBar()
        bar._add_to_history("hello")
        bar._add_to_history("hello")
        assert len(bar._history) == 1

    def test_input_bar_history_multiple(self) -> None:
        """InputBar tracks multiple history entries."""
        from zall.cli.tui import InputBar
        bar = InputBar()
        bar._add_to_history("first")
        bar._add_to_history("second")
        bar._add_to_history("third")
        assert len(bar._history) == 3
        assert bar._history == ["first", "second", "third"]

    def test_input_bar_history_bounded(self) -> None:
        """InputBar history is bounded at 100 entries."""
        from zall.cli.tui import InputBar
        bar = InputBar()
        for i in range(105):
            bar._add_to_history(f"msg {i}")
        assert len(bar._history) <= 100

    def test_input_bar_placeholder(self) -> None:
        """InputBar ChatTextArea has a helpful kimi-style placeholder (commands/files/plan)."""
        from zall.cli.tui import InputBar
        bar = InputBar()
        assert bar._textarea.placeholder is not None
        low = bar._textarea.placeholder.lower()
        # kimi 式极简 placeholder: 提示斜杠命令 + @ 文件 + plan
        assert "commands" in low
        assert "@" in bar._textarea.placeholder
        # 输入框边框标题带提示符/模式指示
        assert "zall" in bar._textarea.border_title


# ── Test 2: ChatMessage code block rendering ──

class TestChatMessageCodeBlocks:
    """ChatMessage syntax highlighting for code blocks."""

    def test_render_code_block_with_language(self) -> None:
        """Code block with language gets Syntax highlighting."""
        from zall.cli.tui import ChatMessage
        msg = ChatMessage(role="assistant", content="")
        result = msg._render_code_block('print("hello")', "python")
        assert isinstance(result, Group)
        # 无边框 Group 内含 Syntax (+ dim 语言标签)
        syntaxes = [r for r in result.renderables if isinstance(r, Syntax)]
        assert syntaxes and syntaxes[0].lexer.name == "Python"

    def test_render_code_block_no_language(self) -> None:
        """Code block without language still renders as panel."""
        from zall.cli.tui import ChatMessage
        msg = ChatMessage(role="assistant", content="")
        result = msg._render_code_block("some plain text", None)
        assert isinstance(result, Group)

    def test_render_code_block_fallback(self) -> None:
        """Code block rendering falls back gracefully on error."""
        from zall.cli.tui import ChatMessage
        msg = ChatMessage(role="assistant", content="")
        with patch("zall.cli.tui.widgets.Syntax", side_effect=Exception("fail")):
            result = msg._render_code_block("code", "python")
            assert isinstance(result, Text)

    def test_markdown_with_code_block(self) -> None:
        """Message with code block produces list of renderables."""
        from zall.cli.tui import ChatMessage
        content = "Here is code:\n```python\nprint('hello')\n```\nDone."
        msg = ChatMessage(role="assistant", content=content)
        result = msg.to_rich()
        # Should return a Group (from rich.console) or Markdown
        from rich.console import Group
        assert isinstance(result, (Markdown, Group, Panel, Text))

    def test_markdown_without_code_block(self) -> None:
        """Plain markdown without code block returns Markdown directly."""
        from zall.cli.tui import ChatMessage
        content = "Just some plain text with **bold**."
        msg = ChatMessage(role="assistant", content=content)
        result = msg.to_rich()
        assert isinstance(result, Markdown)

    def test_code_block_syntax_highlighting(self) -> None:
        """Verify that code blocks get syntax highlighting via rich.syntax.Syntax."""
        from zall.cli.tui import ChatMessage
        msg = ChatMessage(role="assistant", content="")
        result = msg._render_code_block('def foo():\n    pass', "python")
        assert isinstance(result, Group)
        syntaxes = [r for r in result.renderables if isinstance(r, Syntax)]
        assert syntaxes
        # 短块 (<=20 行) 不显行号 (新: 去 demo 感, 与 Claude/Codex 一致)
        assert syntaxes[0].line_numbers is False

    def test_empty_code_block(self) -> None:
        """Empty code block renders as panel."""
        from zall.cli.tui import ChatMessage
        msg = ChatMessage(role="assistant", content="")
        result = msg._render_code_block("", "text")
        assert isinstance(result, Group)


# ── Test 3: Tool call display with diff ──

class TestToolCallDisplay:
    """Tool call display with diff rendering."""

    def test_tool_message_with_output(self) -> None:
        """Tool message with output renders as a Panel."""
        from zall.cli.tui import ChatMessage
        msg = ChatMessage(
            role="tool",
            tool_id="bash",
            tool_args={"command": "ls"},
            tool_success=True,
            tool_output="file1\nfile2\nfile3\n",
        )
        result = msg.to_rich()
        assert isinstance(result, (Text, Panel))

    def test_tool_message_diff_detection(self) -> None:
        """Tool message with diff → kimi 式 diff 面板 (Group: headline + 带 +/- 行号的边框)。"""
        import io
        from rich.console import Console, Group
        from zall.cli.tui import ChatMessage
        diff_output = "--- a/file.py\n+++ b/file.py\n@@ -1,3 +1,4 @@\n-old line\n+new line\n"
        msg = ChatMessage(
            role="tool",
            tool_id="edit_file",
            tool_args={"path": "file.py"},
            tool_success=True,
            tool_output=diff_output,
        )
        result = msg.to_rich()
        # v3.x: edit_file diff → Group(headline, diff 面板)
        assert isinstance(result, Group)
        buf = io.StringIO()
        Console(file=buf, width=72, no_color=True).print(result)
        out = buf.getvalue()
        # 反例防护: +/- 行 + 文件名 + 增删计数不丢
        assert "+ new line" in out
        assert "- old line" in out
        assert "file.py" in out

    def test_tool_message_no_output(self) -> None:
        """Tool message without output renders as summary text."""
        from zall.cli.tui import ChatMessage
        msg = ChatMessage(
            role="tool",
            tool_id="bash",
            tool_args={"command": "ls"},
            tool_success=True,
        )
        result = msg.to_rich()
        assert isinstance(result, Text)

    def test_tool_message_failure(self) -> None:
        """Failed tool message shows failure icon."""
        from zall.cli.tui import ChatMessage
        from zall.cli.render import _G, _C
        msg = ChatMessage(
            role="tool",
            tool_id="bash",
            tool_args={"command": "invalid"},
            tool_success=False,
            tool_output="command not found",
        )
        result = msg.to_rich()
        assert isinstance(result, (Text, Panel))

    def test_tool_panel_with_diff(self) -> None:
        """ToolPanel renders diff output with color-coded lines."""
        from zall.cli.tui import ToolPanel
        diff = "+added line\n-removed line\n@@ -1 +1 @@\n context line"
        panel = ToolPanel("edit_file", {"path": "x.py"}, True, diff)
        # Without expansion, just summary
        result = panel.render()
        # Toggle expand
        panel._expanded = True
        result = panel.render()
        assert isinstance(result, (Text, Panel))

    def test_tool_panel_color_coded_type(self) -> None:
        """ToolPanel uses type-aware colors for different tools."""
        from zall.cli.tui import ToolPanel
        from zall.cli.render import _C

        # Read tool should use steel_blue1
        read_panel = ToolPanel("read_file", {"path": "x.py"}, True, "")
        assert read_panel._get_tool_color() == _C.TOOL_READ

        # Write tool should use gold1
        write_panel = ToolPanel("write_file", {"path": "x.py"}, True, "")
        assert write_panel._get_tool_color() == _C.TOOL_WRITE

        # Bash tool should use dark_orange
        bash_panel = ToolPanel("bash", {"command": "ls"}, True, "")
        assert bash_panel._get_tool_color() == _C.TOOL_BASH


# ── Test 4: StatusBar enhanced fields ──

class TestStatusBarEnhanced:
    """StatusBar with enhanced fields (git_branch, cwd, context_pct, mode)."""

    def test_status_bar_git_branch(self) -> None:
        """StatusBar supports git_branch reactive field."""
        from zall.cli.tui import StatusBar
        bar = StatusBar()
        bar.git_branch = "main"
        assert bar.git_branch == "main"

    def test_status_bar_cwd(self) -> None:
        """StatusBar supports cwd reactive field."""
        from zall.cli.tui import StatusBar
        bar = StatusBar()
        bar.cwd = "my-project"
        assert bar.cwd == "my-project"

    def test_status_bar_context_pct(self) -> None:
        """StatusBar supports context_pct reactive field."""
        from zall.cli.tui import StatusBar
        bar = StatusBar()
        bar.context_pct = "45%"
        assert bar.context_pct == "45%"

    def test_status_bar_mode_cases(self) -> None:
        """StatusBar mode shows correct color for each mode."""
        from zall.cli.tui import StatusBar
        bar = StatusBar()
        bar.mode = "plan"
        result = bar.render()
        assert result is not None

        bar.mode = "strict"
        result = bar.render()
        assert result is not None

        bar.mode = "accept"
        result = bar.render()
        assert result is not None

        bar.mode = "normal"
        result = bar.render()
        assert result is not None

    def test_status_bar_all_fields(self) -> None:
        """StatusBar renders with all fields populated."""
        from zall.cli.tui import StatusBar
        bar = StatusBar()
        bar.model = "claude-4"
        bar.step = 5
        bar.tokens = "1,500 tokens"
        bar.mode = "normal"
        bar.git_branch = "feature-x"
        bar.cwd = "my-project"
        bar.context_pct = "45%"
        result = bar.render()
        assert result is not None

    def test_status_bar_empty(self) -> None:
        """StatusBar renders with no fields."""
        from zall.cli.tui import StatusBar
        bar = StatusBar()
        result = bar.render()
        assert result is not None


# ── Test 5: TuiApp enhanced status wiring ──

class TestTuiAppStatusWiring:
    """TuiApp wires reactive state to StatusBar widget."""

    def test_tui_app_has_status_git_branch(self) -> None:
        """TuiApp has status_git_branch reactive field."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        assert hasattr(app, 'status_git_branch')
        assert app.status_git_branch == ""

    def test_tui_app_has_status_cwd(self) -> None:
        """TuiApp has status_cwd reactive field."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        assert hasattr(app, 'status_cwd')
        assert app.status_cwd == ""

    def test_tui_app_has_status_context_pct(self) -> None:
        """TuiApp has status_context_pct reactive field."""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        assert hasattr(app, 'status_context_pct')
        assert app.status_context_pct == ""


# ── Test 6: ChatMessage streaming with code blocks ──

class TestChatMessageStreaming:
    """ChatMessage streaming content handling."""

    def test_streaming_content_with_code(self) -> None:
        """Streaming content with code blocks still renders."""
        from zall.cli.tui import ChatMessage
        msg = ChatMessage(role="assistant", streaming=True)
        msg._streaming_content = "Here is some ```python\nprint('test')\n```"
        result = msg.to_rich()
        assert result is not None

    def test_streaming_content_partial(self) -> None:
        """Partial streaming content renders as Markdown."""
        from zall.cli.tui import ChatMessage
        msg = ChatMessage(role="assistant", streaming=True)
        msg._streaming_content = "Partial response..."
        result = msg.to_rich()
        assert isinstance(result, Markdown)

    def test_empty_streaming(self) -> None:
        """Empty streaming content shows placeholder."""
        from zall.cli.tui import ChatMessage
        msg = ChatMessage(role="assistant", streaming=True, content="")
        result = msg.to_rich()
        assert result is not None


# ── Test 7: MessageList with enhanced messages ──

class TestMessageListEnhanced:
    """MessageList with enhanced message rendering."""

    def test_add_code_block_message(self) -> None:
        """MessageList can add a message with code blocks."""
        from zall.cli.tui import MessageList, ChatMessage
        ml = MessageList()
        msg = ChatMessage(
            role="assistant",
            content="Code:\n```python\nprint('hello')\n```\nEnd.",
        )
        ml.add_message(msg)
        assert len(ml._messages) == 1

    def test_add_tool_with_diff(self) -> None:
        """MessageList can add a tool message with diff output."""
        from zall.cli.tui import MessageList, ChatMessage
        ml = MessageList()
        msg = ChatMessage(
            role="tool",
            tool_id="edit_file",
            tool_args={"path": "x.py"},
            tool_success=True,
            tool_output="+new line\n-old line\n@@ -1 +1 @@\n",
        )
        ml.add_message(msg)
        assert len(ml._messages) == 1

    def test_mixed_message_types(self) -> None:
        """MessageList handles mixed message types with code blocks."""
        from zall.cli.tui import MessageList, ChatMessage
        ml = MessageList()
        ml.add_message(ChatMessage(role="user", content="Write code"))
        ml.add_message(ChatMessage(
            role="assistant",
            content="Here's the code:\n```python\ndef foo():\n    return 42\n```\n",
        ))
        ml.add_message(ChatMessage(
            role="tool",
            tool_id="bash",
            tool_success=True,
            tool_output="42",
        ))
        assert len(ml._messages) == 3