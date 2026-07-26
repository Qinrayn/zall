"""zall TUI (Terminal User Interface) package.

Provides a full-screen textual-based TUI that replaces the line-based
CliRenderer when running interactively in a TTY.

Exports:
  - TuiApp: the main textual App subclass
  - run_tui: factory function to create and run the TUI
  - ChatMessage: message model for the chat history
  - MessageList: scrollable message history widget
  - InputBar: multi-line input widget
  - StatusBar: status display widget
"""

from __future__ import annotations

from zall.cli.tui.app import TuiApp, _check_tui_supported, _detect_terminal_capabilities, run_tui
from zall.cli.tui.widgets import (
    ChatMessage,
    InputBar,
    MessageList,
    StatusBar,
    ThinkingPanel,
    ToolPanel,
)

__all__ = [
    "ChatMessage",
    "InputBar",
    "MessageList",
    "StatusBar",
    "ThinkingPanel",
    "ToolPanel",
    "TuiApp",
    "run_tui",
]