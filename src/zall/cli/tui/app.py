"""zall.cli.tui.app — Full-screen TUI application using textual.

The TuiApp is a textual App subclass that provides a full-screen terminal UI
for interacting with zall's AgentLoop. It replaces the line-based CliRenderer
when running in a TTY with textual available.

Design (inspired by Kimi Code + Claude Code):
  - Top: message history (scrollable) with Markdown rendering
  - Bottom: multi-line input bar (Shift+Enter for newline, Enter to submit)
  - Status bar: model name, step count, token usage, permission mode
  - Streaming: real-time token updates via textual's reactive system
  - Interrupt: Ctrl+C during generation preserves partial output
  - Collapsible: tool calls and thinking blocks are expandable on click

Thread safety:
  - The AgentLoop runs in a textual worker (background thread)
  - Events are posted back to the main thread via call_from_thread()
  - UI updates happen only in the main thread
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any, Callable

from rich.text import Text
from textual.app import App, ComposeResult
from textual.reactive import reactive
from textual.widgets import Footer
from textual.binding import Binding
from textual.theme import Theme
from textual import work

from zall.core.loop_events import LoopEvent
from zall.cli.render import _C, _G, _key_arg, _unicode_supported, use_ascii_glyphs
from zall.cli.tui.widgets import (
    ChatMessage,
    MessageList,
    LiveRegion,
    InputBar,
    StatusBar,
)


# ── Terminal capability detection (cross-platform) ──

def _detect_terminal_capabilities() -> dict[str, bool]:
    """Detect terminal capabilities for cross-platform TUI compatibility.

    Returns a dict: tui_supported / unicode / colors / ansi (all bool).
    非 TTY / dumb / CI → 退让行式 REPL; 已知现代终端 (跨平台) → 明确支持;
    交互 TTY 默认支持 (Textual 自身再做能力探测与优雅降级)。
    """
    caps: dict[str, bool] = {
        "tui_supported": True,
        "unicode": _unicode_supported(),
        "colors": True,
        "ansi": True,
    }

    # 非 TTY (管道/重定向) — 无法跑全屏 TUI, 退让
    if not sys.stdout.isatty():
        caps["tui_supported"] = False
        return caps

    term = os.environ.get("TERM", "").lower()
    term_program = os.environ.get("TERM_PROGRAM", "")
    colorterm = os.environ.get("COLORTERM", "").lower()

    # dumb 终端: 无 ANSI/颜色/TUI
    if term == "dumb":
        caps["tui_supported"] = False
        caps["ansi"] = False
        caps["colors"] = False
        return caps

    # emacs 内置终端: 不跑全屏 TUI (行式更稳)
    if term == "emacs":
        caps["tui_supported"] = False
        return caps

    # CI 环境: 多为非交互 (即使 isatty), 退让行式 REPL
    if os.environ.get("CI"):
        caps["tui_supported"] = False
        return caps

    # truecolor 提示
    if colorterm in ("truecolor", "24bit"):
        caps["colors"] = True

    # 已知现代终端 (跨平台标记) — 明确支持全屏 TUI
    _modern_env = (
        "WT_SESSION",          # Windows Terminal
        "VSCODE_INJECTION",    # VS Code / Qoder 内置终端
        "ConEmuANSI",          # ConEmu / Cmder
        "WEZTERM_EXECUTABLE",  # WezTerm
        "KITTY_WINDOW_ID",     # kitty
        "ALACRITTY_SOCKET",    # Alacritty
    )
    _modern_program = (
        "iTerm.app", "Apple_Terminal", "WezTerm", "Hyper",
        "vscode", "Tabby", "rio", "ghostty",
    )
    if (any(os.environ.get(k) for k in _modern_env)
            or term_program in _modern_program
            or term.startswith((
                "xterm", "screen", "tmux", "rxvt", "vt", "linux",
                "alacritty", "wezterm", "kitty", "foot", "st",
            ))):
        return caps

    # Windows 无 TERM 且无现代标记: Win10 1809+ 控制台默认支持 VT;
    # 交互 TTY 已确认 → 默认支持 (旧 ConHost 极少见, Textual 会优雅降级)。
    # 其余 POSIX 交互终端: 默认支持。
    return caps


def _check_tui_supported() -> bool:
    """Check if the current terminal supports full-screen TUI mode.

    Returns True if TUI can run, False to fallback to line-based REPL.
    """
    caps = _detect_terminal_capabilities()
    return caps.get("tui_supported", False)


# zall 主题 — 色值从 cli/theme.py 生效主题派生 (G6 单一色源)。
# Textual 主题名固定 "zall" (不随色板名变, 避免 CSS 变量引用断裂);
# 换肤 = 换色板来源 (env ZALL_THEME / config [ui].theme), 重启 TUI 生效。
def _build_zall_theme() -> Theme:
    from zall.cli import theme as _theme_mod
    _t = _theme_mod.active()
    return Theme(
        name="zall",
        primary=_t.tui_primary,
        accent=_t.tui_accent,
        secondary=_t.tui_secondary,
        background=_t.tui_background,
        surface=_t.tui_surface,
        panel=_t.tui_panel,
        foreground=_t.tui_foreground,
        success=_t.tui_success,
        warning=_t.tui_warning,
        error=_t.tui_error,
        dark=True,
    )


_ZALL_THEME = _build_zall_theme()


# ──────────────────────────────────────────────────────────────────────────
# TuiApp — full-screen textual App
# ──────────────────────────────────────────────────────────────────────────

class TuiApp(App):
    """Full-screen terminal UI for zall AgentLoop interaction.

    Provides a multi-panel TUI with message history, input bar, and status bar.
    Replaces CliRenderer when running interactively in a TTY.

    Usage:
        app = TuiApp()
        app.run()  # blocks until user exits

    Event-driven:
        app(event)  # __call__ interface, thread-safe, can be called from any thread
    """

    __test__ = False

    # ── App metadata ──
    TITLE = "zall"
    SUB_TITLE = "coding agent"

    # ── CSS ── (颜色全部走 zall 主题变量, 无字面 hex)
    CSS = """
    Screen {
        background: $background;
    }

    TuiApp {
        background: $background;
    }

    MessageList {
        height: 1fr;
        margin: 0 1;
        padding: 1 2;
        border: none;
        background: $background;
        scrollbar-size-vertical: 0;
    }

    MessageList > .rich-log {
        background: $background;
    }

    InputBar {
        height: auto;
        max-height: 60%;
        margin: 0 1 1 1;
        dock: bottom;
        layout: vertical;
    }

    InputBar > ChatTextArea {
        height: 3;
        background: $surface;
        color: $foreground;
        border: solid $panel;
        border-title-align: left;
        border-title-color: $accent;
        border-subtitle-align: right;
        border-subtitle-color: $text-muted;
        padding: 0 2;
        scrollbar-size-vertical: 0;
    }

    InputBar > ChatTextArea:focus {
        border: solid $accent;
        background: $surface;
    }

    StatusBar {
        dock: top;
        height: 1;
        background: $background;
        color: $text-muted;
        padding: 0 1;
    }

    VerticalScroll {
        scrollbar-size-vertical: 0;
    }
    """

    # ── Bindings ──
    # v1.6: 描述文案是给用户看的 (Footer 自动渲染), 写清楚每个键做什么
    # v2.x: priority=True — 输入框(TextArea)聚焦时也生效 (否则键被 TextArea 吞掉 → "按了无效")
    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Interrupt generation", show=True, priority=True),
        Binding("ctrl+d", "exit", "Exit", show=True, priority=True),
        Binding("ctrl+l", "clear_screen", "Clear screen", show=True, priority=True),
        Binding("shift+tab", "toggle_plan", "Plan mode", show=True, priority=True),
        Binding("ctrl+o", "external_editor", "Editor", show=True, priority=True),
        Binding("ctrl+s", "steer", "Steer", show=True, priority=True),
        Binding("ctrl+q", "quit", "Quit", show=False, priority=True),
    ]

    # ── Reactive state ──
    status_model: reactive[str] = reactive("")
    status_step: reactive[int] = reactive(0)
    status_tokens: reactive[str] = reactive("")
    status_mode: reactive[str] = reactive("")
    status_git_branch: reactive[str] = reactive("")
    status_cwd: reactive[str] = reactive("")
    status_context_pct: reactive[str] = reactive("")
    status_queued: reactive[int] = reactive(0)
    _is_streaming: reactive[bool] = reactive(False)

    def __init__(
        self,
        *,
        model: str | None = None,
        yes: bool = False,
        verbose: bool = False,
        strict: bool = False,
        resume_session: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        # 单一 zall 主题 (调色板真源); 在 __init__ 注册+启用避免首帧闪色
        self.register_theme(_ZALL_THEME)
        self.theme = "zall"
        self._resume_session = resume_session  # CLI --continue/-r: on_mount 恢复
        # v2.x fix: --model 未指定时从 config 解析真实 model。
        # 原来 self._model = model or "" → 未传 --model 时永远显示 "unset"
        # (即使 config 已配置); 且 state["model"]="" 会让 build_repl_loop 的
        # provider 检测退化为 openai (而非实际 provider)。
        if not model:
            try:
                from zall.cli.config import _config_status
                model = _config_status().get("model") or ""
            except Exception:
                model = ""
        self._model = model or ""
        self._yes = yes
        self._verbose = verbose
        self._strict = strict
        self._agent_loop: Any = None  # AgentLoop, imported lazily
        # v1.9: 初始化基础 state — 让斜杠命令在首次对话前也能用 (如 /model /cost)
        self._state: dict[str, Any] = {
            "model": self._model,
            "max_steps": 100_000,
            "verbose": verbose,
            "usage": {"prompt": 0, "completion": 0},
        }
        self._loop_thread: threading.Thread | None = None
        self._interrupt_requested = False
        self._event_queue: list[LoopEvent] = []
        self._event_lock = threading.Lock()
        # v2.x (kimi parity): steer/queue — Ctrl+S 注入当前回合, Enter(流式中) 排队
        self._pending_queue: list[str] = []
        self._steer_queue: list[str] = []
        self._queue_lock = threading.Lock()
        self._agent_running: bool = False
        # v2.x: 确认门 (greylist/blacklist) — 输入路由到确认回答, 解除 worker 阻塞
        self._confirm_active: bool = False
        self._confirm_responder: Any = None
        self._streaming_step: int | None = None
        self._streaming_content: str = ""
        self._thinking_active: bool = False  # v2.x: 思考流式累积中 (Claude Code 风格)
        self._null_sink: Any = None  # Bug6: devnull 输出流引用, 退出时关闭
        self._current_step = 0
        self._tool_calls_in_step: int = 0
        self._model_calls_in_step: int = 0
        self._on_exit: Callable[[], None] | None = None
        # v1.5: EventBus 订阅引用 (防垃圾回收 + 供退出时解绑)
        self._tui_bus: Any = None
        self._tui_listener: Any = None
        # v1.5: 流式重绘节流时间戳 (性能优化)
        self._last_stream_render: float = 0.0
        # v3.x (性能): token/thinking 事件批处理缓冲 — 减少 call_from_thread 跨线程开销
        # (原逐 token ~100+/s 跨线程 → 每批次 ~12/s, 消灭流式卡顿根因)
        self._token_buffer: str = ""
        self._thinking_buffer: str = ""
        self._token_flush_pending: bool = False
        self._thinking_flush_pending: bool = False
        self._buf_lock = threading.Lock()
        # Widget 引用缓存 (compose 后不变, 避免每事件 query_one 开销)
        self._cached_live: Any = None
        self._cached_msg_list: Any = None

        # Lazy import to avoid circular imports at module level
        self._loop_module: Any = None
        self._repl_module: Any = None

    def set_on_exit(self, callback: Callable[[], None]) -> None:
        """Set a callback to be called when the app exits."""
        self._on_exit = callback

    def _sync_status_bar(self) -> None:
        """Sync TuiApp reactive state to the StatusBar widget.
        
        The StatusBar widget has its own reactive fields (model, step, tokens, etc.)
        that are separate from the TuiApp's status_* fields. This method bridges
        them so the StatusBar renders correctly.
        """
        try:
            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.model = self.status_model
            status_bar.step = self.status_step
            status_bar.tokens = self.status_tokens
            status_bar.mode = self.status_mode
            status_bar.git_branch = self.status_git_branch
            status_bar.cwd = self.status_cwd
            status_bar.context_pct = self.status_context_pct
            status_bar.streaming = self._is_streaming  # v1.6: 流式状态同步
            status_bar.queued = self.status_queued  # v2.x: 排队数同步
        except Exception:
            pass
        # kimi 式底栏: 输入框右下角状态行 (cwd · branch · ctx%) — 与顶部状态栏互补
        try:
            parts: list[str] = []
            if self.status_cwd:
                parts.append(self.status_cwd)
            if self.status_git_branch:
                parts.append(self.status_git_branch)
            if self.status_context_pct:
                parts.append(f"ctx {self.status_context_pct}")
            sep = f" {_G.BULLET} "
            line = f" {sep.join(parts)} " if parts else ""
            self.query_one("#input-bar", InputBar).set_status_line(line)
        except Exception:
            pass

    def _update_usage(self, usage: dict) -> None:
        """从 usage 更新 token 计数 + 上下文占用%。

        使用 status_model (实时更新) 而非 self._model (初始化时值),
        确保 /model 切换后 ctx% 显示正确的窗口大小。
        """
        if not usage:
            return
        total = usage.get("total", 0) or (usage.get("prompt", 0) + usage.get("completion", 0))
        if total:
            self.status_tokens = f"{total:,} tokens"
        prompt = int(usage.get("prompt", 0) or 0)
        if prompt:
            try:
                from zall._util.model_registry import get_window_size
                # 用 status_model (实时) 而非 self._model (可能过时)
                model_name = self.status_model or self._model or ""
                window = int(get_window_size(model_name) or 0)
            except Exception:
                window = 0
            if window > 0:
                self.status_context_pct = f"{min(100, prompt * 100 // window)}%"

    # ── Lifecycle ──

    def compose(self) -> ComposeResult:
        yield StatusBar(id="status-bar")
        yield MessageList(id="message-list")
        yield LiveRegion(id="live-region")
        yield InputBar(id="input-bar")
        # v1.6: textual Footer 自动渲染 BINDINGS 键位提示 — 解决 "不知道咋用"
        yield Footer()

    def _build_welcome_renderable(self) -> Any:
        """简洁欢迎屏 (kimi 风格: 纯文字 + 克制色彩, 无图形方块噪声)。"""
        from rich.console import Group
        from rich.panel import Panel
        from rich import box
        from zall import __version__
        A, DIM, SUB, INFO = _C.ACCENT, _C.DIM, _C.SUBTLE, _C.INFO
        # 标识 + 版本
        title = Text()
        title.append("zall", style=f"bold {A}")
        title.append(f"  v{__version__}", style=SUB)
        # 描述
        desc = Text("a falsifiable, reproducible coding agent", style=SUB)
        # 模型
        model_line = Text(self._model or "unset", style=DIM)
        # 键位提示
        hint = Text()
        hint.append("Enter", style=f"bold {INFO}"); hint.append(" send   ", style=DIM)
        hint.append("/help", style=INFO); hint.append(" commands   ", style=DIM)
        hint.append("@", style=INFO); hint.append(" files   ", style=DIM)
        hint.append("Shift+Tab", style=INFO); hint.append(" plan   ", style=DIM)
        hint.append("esc", style=INFO); hint.append(" interrupt", style=DIM)
        return Panel(
            Group(title, desc, model_line, Text(""), hint),
            border_style=A, box=box.ROUNDED, padding=(1, 2), expand=False,
        )

    def on_mount(self) -> None:
        """Called when the app is mounted and ready."""
        # 填充 widget 缓存 (compose 后立即可用)
        self._cached_msg_list = self.query_one("#message-list", MessageList)
        self._cached_live = self.query_one("#live-region", LiveRegion)
        # 欢迎屏
        self._cached_msg_list.write(self._build_welcome_renderable())
        # Focus the input bar
        self.query_one("#input-bar", InputBar).focus()

        # Update status bar
        self.status_model = self._model or "unset"
        # Set cwd and git branch (Claude Code inspired: shows workspace context)
        self.status_cwd = os.path.basename(os.getcwd())
        try:
            import subprocess
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                self.status_git_branch = result.stdout.strip()
        except Exception:
            pass
        self._sync_status_bar()

        # CLI --continue/-r: 恢复上一会话上下文 (首个回合构建 loop 时 seed)
        if self._resume_session:
            self._resume_prior_session(self._resume_session)

    def _resume_prior_session(self, session_id: str) -> None:
        """恢复指定会话的上下文到 state (agent 首个回合可见历史); 显示系统提示。"""
        import io as _io
        from zall.cli.session import _run_resume
        try:
            _run_resume(_io.StringIO(), session_id, self._state)
        except Exception:
            self._show_system(f"could not resume session {session_id[:8]}")
            return
        msgs = self._state.get("resume_messages")
        n = len(msgs) if msgs else 0
        if n:
            self._show_system(f"resumed session {session_id[:8]} ({n} messages restored)")
        else:
            self._show_system(f"could not resume session {session_id[:8]}")

    # ── Input handling ──

    def on_input_bar_submitted(self, event: InputBar.Submitted) -> None:
        """Handle user input submission from InputBar.

        v2.x: 流式中 (agent 正忙) 的普通消息进入队列, 当前回合结束后自动运行 (B)。
        斜杠命令始终立即执行 (多为显示类)。
        """
        text = event.text
        if not text:
            return
        # v2.x: 确认门激活中 → 本次提交作为权限回答 (不进 agent / 不排队)
        if self._confirm_active and self._confirm_responder is not None:
            self._confirm_active = False
            msg_list = self._get_msg_list()
            if msg_list is not None:
                msg_list.add_message(ChatMessage(role="user", content=text))
            self._confirm_responder.submit_reply(text)
            return
        # 斜杠命令: 始终立即执行 (显示 user 气泡)
        if text.startswith("/"):
            msg_list = self._get_msg_list()
            if msg_list is not None:
                msg_list.add_message(ChatMessage(role="user", content=text))
            self._handle_slash_command(text)
            return
        # B (queue): 有回合在跑 → 排队, 不立即显示气泡 (运行时再 echo)
        if self._agent_running:
            self._enqueue_pending(text)
            return
        # 空闲 → 立即运行
        msg_list = self._get_msg_list()
        if msg_list is not None:
            msg_list.add_message(ChatMessage(role="user", content=text))
        self._agent_running = True
        self._run_agent_loop(text)

    def on_input_bar_toggle_plan(self, event: InputBar.TogglePlan) -> None:
        """Shift+Tab from the input bar → toggle plan mode."""
        self.action_toggle_plan()

    def on_input_bar_open_editor(self, event: InputBar.OpenEditor) -> None:
        """Ctrl+O from the input bar → open external editor."""
        self.action_external_editor()

    def on_input_bar_steer(self, event: InputBar.Steer) -> None:
        """Ctrl+S from the input bar → steer into the running turn."""
        self._do_steer(event.text)

    def action_steer(self) -> None:
        """Ctrl+S 绑定 (焦点不在输入框时): 取输入框文本并 steer。"""
        text = ""
        try:
            input_bar = self.query_one("#input-bar", InputBar)
            text = input_bar._textarea.text
            input_bar._textarea.clear()
        except Exception:
            text = ""
        self._do_steer(text)

    # ── steer / queue plumbing (A/B, kimi parity) ──

    def _do_steer(self, text: str) -> None:
        """Ctrl+S: 将文本注入正在运行的回合; 无活动回合则等同普通提交。"""
        text = (text or "").strip()
        if not self._agent_running:
            if text:
                msg_list = self._get_msg_list()
                if msg_list is not None:
                    msg_list.add_message(ChatMessage(role="user", content=text))
                self._agent_running = True
                self._run_agent_loop(text)
            return
        if not text:
            # 空 steer + 有排队 → 弹出最早一条来 steer
            text = self._pop_pending() or ""
            self._update_queue_status()
            if not text:
                return
        self._push_steer(text)
        self._show_system(f"steer \u2192 injected into current turn: {text[:60]}")

    def _enqueue_pending(self, text: str) -> None:
        """Enter(流式中): 把消息排到当前回合之后。"""
        with self._queue_lock:
            self._pending_queue.append(text)
            n = len(self._pending_queue)
        self._show_system(f"queued \u2192 runs after current turn ({n} pending)")
        self._update_queue_status()

    def _pop_pending(self) -> str | None:
        with self._queue_lock:
            if self._pending_queue:
                return self._pending_queue.pop(0)
        return None

    def _push_steer(self, text: str) -> None:
        with self._queue_lock:
            self._steer_queue.append(text)

    def _pop_steer_messages(self) -> list[str]:
        with self._queue_lock:
            msgs = list(self._steer_queue)
            self._steer_queue.clear()
        return msgs

    def _update_queue_status(self) -> None:
        with self._queue_lock:
            n = len(self._pending_queue)
        self.status_queued = n
        self._sync_status_bar()

    def _echo_queued_turn(self, text: str) -> None:
        """排队消息开始运行时: 显示 user 气泡 + 刷新排队计数。"""
        msg_list = self._get_msg_list()
        if msg_list is not None:
            msg_list.add_message(ChatMessage(role="user", content=text))
        self._update_queue_status()

    def _handle_slash_command(self, text: str) -> None:
        """v1.9: 路由到真实命令注册表 (修复: 之前只硬编码 3 个命令)。

        之前 TUI 只处理 /help /clear /exit, 其余 54 个命令全报 unknown —
        但命令菜单显示了全部 57 个。现在统一走 handle_slash (与 REPL 一致),
        命令输出捕获到 StringIO 后以系统消息呈现。
        """
        import io
        from zall.cli.commands import handle_slash
        msg_list = self._get_msg_list()
        if msg_list is None:
            return
        buf = io.StringIO()
        try:
            action = handle_slash(text, self._state, buf, self._agent_loop)
        except Exception as e:
            self._show_error(f"command failed: {e}")
            return
        output = buf.getvalue().strip()
        if output:
            msg_list.add_message(ChatMessage(role="system", content=output))
        if action == "exit":
            self.action_exit()
        elif action == "clear":
            msg_list.clear()
            msg_list._messages = []
            self._agent_loop = None
            msg_list.write(self._build_welcome_renderable())

    # ── Agent loop execution ──

    @work(exclusive=True, thread=True)
    async def _run_agent_loop(self, user_input: str) -> None:
        """Run the AgentLoop in a background worker thread.

        This runs in a textual worker (background thread) and posts events
        back to the main thread via call_from_thread().
        """
        # Import lazily to avoid circular imports
        from zall.cli.repl_ui import build_repl_loop, is_transient_error

        # Reset streaming state
        self._interrupt_requested = False
        self._streaming_step = None
        self._streaming_content = ""
        self._tool_calls_in_step = 0
        self._model_calls_in_step = 0
        self._thinking_active = False  # Bug3: 每轮重置, 防上轮残留卡在 "Thinking\u2026"

        # v2.x: @file 引用展开 — @path 真实文件内容注入 (Claude Code 式), 与 REPL 一致。
        # 在 worker 线程读文件 (安全); 非文件 @token 原样保留。用户气泡已显原文, 模型收到展开后。
        from zall.cli.file_complete import expand_at_references
        user_input, _injected = expand_at_references(user_input)
        if _injected:
            self.call_from_thread(
                self._show_system,
                f"injected {len(_injected)} file(s): {', '.join(_injected[:5])}",
            )

        # Build the loop if needed
        if self._agent_loop is None:
            # We need to import these here to avoid circular imports
            from zall.skills import load_skills
            from zall.cli.orchestrator import build_mcp_tools

            mcp_tools = build_mcp_tools(sys.stderr)
            skills = load_skills()

            # Build state
            self._state = {
                "model": self._model,
                "max_steps": 100_000,
                "verbose": self._verbose,
                "usage": {"prompt": 0, "completion": 0},
                "_mcp_tools": mcp_tools,
                "_skills": skills,
            }

            # TUI 模式: CliRenderer 输出全部静默 (TuiApp 自己处理事件渲染)
            # 用 devnull 避免 CliRenderer 的 spinner/step_progress 破坏 TUI 界面
            import os as _os
            _null = open(_os.devnull, "w", encoding="utf-8") if _os.devnull else sys.stderr
            self._null_sink = _null  # Bug6: 持有引用以便退出时关闭
            # v2.x: TUI 专用确认门 responder (修复 greylist 确认挂死)
            from zall.cli.tui.tui_responder import TuiUserResponder
            self._confirm_responder = TuiUserResponder(
                self, yes=self._yes, plan_mode=self._state.get("plan_mode", False)
            )
            loop = build_repl_loop(
                user_input,
                self._state,
                yes=self._yes,
                json_mode=False,
                stream=True,
                out=_null,
                verbose=False,  # TUI 模式不用 verbose
                strict=self._strict,
                responder=self._confirm_responder,
            )
            if loop is None:
                self.call_from_thread(self._show_error, "Failed to build agent loop")
                self._agent_running = False  # Bug4: 早返回也须复位, 防卡死
                return
            self._agent_loop = loop
            # v1.5 修复致命 BUG: 把 TuiApp 接到 loop 的 EventBus。
            # 之前 TUI 用 CliRenderer(输出 devnull) 作 observer, TuiApp 从未收到
            # 任何事件 — 导致全屏模式不显示任何回复/工具/流式输出。
            # EventBus handler 签名: (kind, payload) -> None
            try:
                from zall.core.loop_events import LoopEvent as _LE
                _bus = getattr(loop, "_event_bus", None)
                if _bus is not None:
                    def _tui_listener(kind: str, payload: dict) -> None:
                        # v3.x 性能: 高频 token/thinking 事件批处理
                        # 不逐 token 跨线程, 累积后一次性推送 UI
                        # 死锁修复 (2026-07-26, TUI pilot e2e 钓出):
                        # call_from_thread 阻塞等主线程回调完成, 而回调
                        # (_flush_*_buffer) 首行就要拿 _buf_lock — 若在锁内
                        # 调度则 worker 持锁等主线程 / 主线程等锁, 100% 互死。
                        # 故: 锁内只置标志, call_from_thread 必须在锁外。
                        if kind == "model_token":
                            token = payload.get("token", "")
                            if token:
                                schedule = False
                                with self._buf_lock:
                                    self._token_buffer += token
                                    if not self._token_flush_pending:
                                        self._token_flush_pending = True
                                        schedule = True
                                if schedule:
                                    try:
                                        self.call_from_thread(self._flush_token_buffer)
                                    except Exception:
                                        with self._buf_lock:
                                            self._token_flush_pending = False
                            return
                        if kind == "model_thinking":
                            token = payload.get("token", "")
                            if token:
                                schedule = False
                                with self._buf_lock:
                                    self._thinking_buffer += token
                                    if not self._thinking_flush_pending:
                                        self._thinking_flush_pending = True
                                        schedule = True
                                if schedule:
                                    try:
                                        self.call_from_thread(self._flush_thinking_buffer)
                                    except Exception:
                                        with self._buf_lock:
                                            self._thinking_flush_pending = False
                            return
                        # 其他事件: 低频, 正常路径
                        ev = _LE(kind=kind, step=payload.get("step", 0), payload=payload)
                        try:
                            self.call_from_thread(self._handle_event, ev)
                        except RuntimeError:
                            try:
                                self._handle_event(ev)
                            except Exception:
                                pass
                        except Exception:
                            pass
                    _bus.on("*", _tui_listener)
                    self._tui_bus = _bus
                    self._tui_listener = _tui_listener
            except Exception:
                pass
        else:
            self._agent_loop.add_user_message(user_input)

        # Run the loop
        loop = self._agent_loop
        renderer = self._state.get("_renderer")
        if renderer is not None and hasattr(renderer, "update_status"):
            from zall.cli.environment import get_cached_cwd_meta
            meta = get_cached_cwd_meta(self._state)
            renderer.update_status(
                model=self._state.get("model", "") or str(getattr(loop.model_adapter, "model_name", "")),
                branch=meta.git_branch or "",
                goal=loop.goal.statement.goal_type.value if hasattr(loop.goal, "statement") else "",
                plan=self._state.get("plan_mode", False),
            )

        # Run steps — v2.x: 外层循环排空 pending 队列 (B); 每步前注入 steer (A)
        self._agent_running = True
        try:
            while True:
                turn_ended_cleanly = True
                while True:
                    if self._interrupt_requested:
                        self.call_from_thread(self._show_interrupt)
                        turn_ended_cleanly = False
                        break

                    # A (steer): 步前注入 Ctrl+S 消息 (mid-turn, 下一步即可见)
                    for _sm in self._pop_steer_messages():
                        loop.add_user_message(_sm)

                    try:
                        result = loop.step()
                    except KeyboardInterrupt:
                        self.call_from_thread(self._show_interrupt)
                        turn_ended_cleanly = False
                        break
                    except Exception as e:
                        self.call_from_thread(self._show_error, str(e))
                        turn_ended_cleanly = False
                        break

                    # 修复"失败后中途就停": 瞬态错误(429/5xx/timeout)退避重试而非直接终止 turn。
                    # 在慢/抖的端点上, 一次瞬态错误不应杀死整个多步任务。
                    if (result.is_terminal and result.egress
                            and is_transient_error(result.egress.error)):
                        result = self._retry_transient(loop, result)

                    if result.is_terminal:
                        if result.egress and result.egress.error:
                            self.call_from_thread(self._show_error, result.egress.error)
                        break

                    if result.kind == "awaiting_input":
                        break

                if not turn_ended_cleanly or self._interrupt_requested:
                    break

                # B (queue): 排队消息作为新回合运行
                nxt = self._pop_pending()
                if nxt is None:
                    break
                _msg, _ = expand_at_references(nxt)  # @file 展开 (气泡仍显原文 nxt)
                loop.add_user_message(_msg)
                self._streaming_content = ""
                self.call_from_thread(self._echo_queued_turn, nxt)
        finally:
            self._agent_running = False
            self.call_from_thread(self._update_queue_status)

        # Clean up
        if renderer is not None and hasattr(renderer, "shutdown_spinner"):
            renderer.shutdown_spinner()

    # ── Event handling (thread-safe) ──

    def __call__(self, event: LoopEvent) -> None:
        """Thread-safe event consumption interface.

        Can be called from any thread. Posts the event to the main thread
        for UI updates using call_from_thread.
        """
        # If we're not running, queue the event
        with self._event_lock:
            self._event_queue.append(event)

        # If we're running in the main thread, process immediately
        if hasattr(self, "_running") and self._running:
            try:
                self.call_from_thread(self._handle_event, event)
            except Exception:
                pass

    def handle_loop_event(self, event: LoopEvent) -> None:
        """Handle a LoopEvent for UI updates.

        This is the public API for pushing events into the TuiApp.
        Thread-safe: delegates to __call__.

        Note: renamed from on_event to avoid clash with textual's App.on_event
        (which handles lifecycle events like Mount/Unmount and must return
        an awaitable).
        """
        self(event)

    def _get_live(self) -> Any:
        """取 LiveRegion 活跃区 (缓存, compose 后不变)。"""
        if self._cached_live is not None:
            return self._cached_live
        try:
            self._cached_live = self.query_one("#live-region", LiveRegion)
            return self._cached_live
        except Exception:
            return None

    def _get_msg_list(self) -> Any:
        """取 MessageList (缓存, compose 后不变)。"""
        if self._cached_msg_list is not None:
            return self._cached_msg_list
        try:
            self._cached_msg_list = self.query_one("#message-list", MessageList)
            return self._cached_msg_list
        except Exception:
            return None
    
    def _throttled_live_refresh(self, live: Any) -> None:
        """节流刷新活跃区单块 (80ms; O(1), 非整树重绘)。
    
        旧方案每 token 全量 _rerender_all → 长对话 O(n²) 卡顿; 现只刷新单块。
        """
        now = time.time()
        if (now - self._last_stream_render) >= 0.08:
            self._last_stream_render = now
            live.refresh()

    # ── v3.x 性能: token/thinking 批处理刷新 (call_from_thread 调度次数 100+/s → ~12/s) ──

    def _flush_token_buffer(self) -> None:
        """主线程: 批量刷新累积的流式 token (减少跨线程开销)。"""
        with self._buf_lock:
            chunk = self._token_buffer
            self._token_buffer = ""
            self._token_flush_pending = False
        if not chunk:
            return
        self._streaming_content += chunk
        live = self._get_live()
        if live is None:
            return
        msg = live.message
        if msg is None or msg.role != "assistant":
            # 先固化任何非-assistant 活跃块 (如思考), 再开 assistant 流
            if msg is not None:
                msg_list = self._get_msg_list()
                if msg_list:
                    self._flush_live(msg_list)
            msg = ChatMessage(role="assistant", streaming=True)
            live.set_message(msg)
        msg._streaming_content = self._streaming_content
        self._throttled_live_refresh(live)

    def _flush_thinking_buffer(self) -> None:
        """主线程: 批量刷新累积的思考 token。"""
        with self._buf_lock:
            chunk = self._thinking_buffer
            self._thinking_buffer = ""
            self._thinking_flush_pending = False
        if not chunk:
            return
        live = self._get_live()
        if live is None:
            return
        msg = live.message
        if self._thinking_active and msg is not None and msg.role == "thinking":
            msg.thinking = (msg.thinking or "") + chunk
            self._throttled_live_refresh(live)
        else:
            # 先固化任何非-thinking 活跃块, 再开思考流
            if msg is not None:
                msg_list = self._get_msg_list()
                if msg_list:
                    self._flush_live(msg_list)
            self._thinking_active = True
            m = ChatMessage(role="thinking", thinking=chunk)
            m.thinking_streaming = True
            live.set_message(m)

    def _drain_buffers(self) -> None:
        """排空 token/thinking 缓冲 (model_call 结束时, 确保不丢尾部 token)。"""
        with self._buf_lock:
            tok = self._token_buffer
            thi = self._thinking_buffer
            self._token_buffer = ""
            self._thinking_buffer = ""
            self._token_flush_pending = False
            self._thinking_flush_pending = False
        if thi:
            live = self._get_live()
            if live is not None:
                msg = live.message
                if self._thinking_active and msg is not None and msg.role == "thinking":
                    msg.thinking = (msg.thinking or "") + thi
        if tok:
            self._streaming_content += tok
            live = self._get_live()
            if live is not None:
                msg = live.message
                if msg is not None and msg.role == "assistant":
                    msg._streaming_content = self._streaming_content
    
    def _flush_live(self, msg_list: Any, *, interrupted: bool = False, live: Any = None) -> None:
        """把活跃区当前块固化到历史一次并清空 (kimi flush_content)。
    
        assistant/thinking 空块不固化; interrupted=True 给 assistant 追加中断标记。
        live 可注入 (便于离线单测); 缺省取挂载的 LiveRegion。
        """
        if live is None:
            live = self._get_live()
        if live is None or not live.is_active:
            return
        msg = live.message
        live.clear()
        if msg is None:
            return
        if msg.role == "assistant":
            msg.streaming = False
            content = msg._streaming_content or msg.content
            if interrupted and content:
                content = content + "\n[Interrupted]"
            msg.content = content
            msg._streaming_content = ""
            if not content:
                return
        elif msg.role == "thinking":
            msg.thinking_streaming = False
            self._thinking_active = False
            if not (msg.thinking or "").strip():
                return
        msg_list.add_message(msg)
    
    def _finalize_thinking(self, msg_list: Any, live: Any = None) -> None:
        """思考结束: 把活跃区的思考块固化到历史一次 (非流式定格)。"""
        self._thinking_active = False
        if live is None:
            live = self._get_live()
        if (live is not None and live.is_active
                and live.message is not None and live.message.role == "thinking"):
            self._flush_live(msg_list, live=live)

    def _handle_event(self, event: LoopEvent) -> None:
        """Process a LoopEvent and update the UI (must be called from main thread).
    
        v3.x: model_token/model_thinking 已由批处理路径处理, 不再进入此方法。
        Safe to call even when the app is not mounted (no-op in that case).
        """
        # Guard: skip if not mounted (no screen stack)
        msg_list = self._get_msg_list()
        if msg_list is None:
            self._update_reactive_state(event)
            return
    
        kind = event.kind
        p = event.payload
        step = event.step
        self._current_step = step
        self.status_step = step
    
        # 思考结束 (来了非 thinking/token 事件) → 一次性定格思考块
        if self._thinking_active and kind not in ("model_thinking", "model_token"):
            self._finalize_thinking(msg_list)
    
        if kind == "model_call_start":
            self._is_streaming = True
            self._streaming_step = step
            self._streaming_content = ""
            self._model_calls_in_step += 1
            model_name = p.get("model", "")
            if model_name:
                self.status_model = model_name
            self._sync_status_bar()
    
        elif kind == "model_tool_call":
            # 流式工具调用预览: 簟态, 由 tool_call_start/end 呈现
            pass

        elif kind == "model_token":
            # v3.x: 正常路径走 listener 批处理 (_flush_token_buffer);
            # 此分支保留给直接调用 _handle_event 的场景 (测试/兼容)。
            token = p.get("token", "")
            if token:
                self._streaming_content += token
                live = self._get_live()
                if live is not None:
                    msg = live.message
                    if msg is None or msg.role != "assistant":
                        if msg is not None:
                            self._flush_live(msg_list)
                        msg = ChatMessage(role="assistant", streaming=True)
                        live.set_message(msg)
                    msg._streaming_content = self._streaming_content
                    self._throttled_live_refresh(live)

        elif kind == "model_thinking":
            # v3.x: 同上, 正常路径走 _flush_thinking_buffer。
            token = p.get("token", "")
            if token:
                live = self._get_live()
                if live is not None:
                    msg = live.message
                    if self._thinking_active and msg is not None and msg.role == "thinking":
                        msg.thinking = (msg.thinking or "") + token
                        self._throttled_live_refresh(live)
                    else:
                        if msg is not None:
                            self._flush_live(msg_list)
                        self._thinking_active = True
                        m = ChatMessage(role="thinking", thinking=token)
                        m.thinking_streaming = True
                        live.set_message(m)
    
        elif kind == "model_call":
            # 排空未 flush 的缓冲 (确保不丢尾部 token)
            self._drain_buffers()
    
            reasoning = p.get("reasoning", "")
            content = p.get("content", "")
    
            self._is_streaming = False
    
            # 流式 assistant 内容 → 固化到历史; 非流式 content → 直接追加
            if self._streaming_content:
                self._flush_live(msg_list)
                self._streaming_content = ""
            elif content:
                msg_list.add_message(ChatMessage(role="assistant", content=content))
    
            # reasoning (非流式思考) → 历史
            if reasoning:
                msg_list.add_message(ChatMessage(role="thinking", thinking=reasoning))
    
            # Token usage + 上下文占用%
            self._update_usage(p.get("usage", {}))
            self._sync_status_bar()

        elif kind == "tool_call_start":
            tool_id = p.get("tool_id", "?")
            args = p.get("args", {})
            live = self._get_live()
            if live is not None:
                live.set_message(ChatMessage(
                    role="tool",
                    tool_id=tool_id,
                    tool_args=args,
                ))

        elif kind == "tool_call_end":
            tool_id = p.get("tool_id", "?")
            success = p.get("success", False)
            output = p.get("output", "")
            error = p.get("error")
            body = output or error or "(no output)"
            live = self._get_live()
            if (live is not None and live.is_active
                    and live.message is not None and live.message.role == "tool"):
                live.clear()
            msg_list.add_message(ChatMessage(
                role="tool",
                tool_id=tool_id,
                tool_success=success,
                tool_output=body,
            ))
            self._tool_calls_in_step += 1
            self._sync_status_bar()

        elif kind == "tool_rejected":
            tool_id = p.get("tool_id", "?")
            msg_list.add_message(ChatMessage(
                role="system",
                content=f"{_G.FAIL} {tool_id}: rejected by user",
            ))

        elif kind == "gate_decision":
            level = p.get("level", "")
            tool_id = p.get("tool_id", "?")
            if level == "greylist":
                msg_list.add_message(ChatMessage(
                    role="system",
                    content=f"{_G.WARN} greylist: {tool_id} (needs confirm)",
                ))
            elif level == "blacklist":
                msg_list.add_message(ChatMessage(
                    role="error",
                    content=f"{_G.FAIL} BLACKLIST: {tool_id} (blocked)",
                ))

        elif kind == "judge_result":
            state = p.get("state", "?")
            report = p.get("report", "")
            color = {"met": _C.SUCCESS, "not_met": _C.FAIL, "undecidable": _C.WARN}.get(state, _C.DIM)
            icon = {"met": _G.MET, "not_met": _G.FAIL, "undecidable": _G.UNDECIDABLE}.get(state, _G.BULLET)
            line = f"[{color}]{icon} {state}[/]"
            if report:
                line += f" [{_C.SUBTLE}]{_G.BULLET} {report[:80]}[/]"
            msg_list.add_message(ChatMessage(role="system", content=line))

        elif kind == "override":
            tool_id = p.get("tool_id", "?")
            msg_list.add_message(ChatMessage(
                role="error",
                content=f"{_G.FAIL} {tool_id}: OVERRIDDEN (audit logged)",
            ))

        elif kind == "step_progress":
            # 状态栏 + 流式输出已传达活动状态, 不入消息流
            pass

        elif kind == "context_compaction":
            count = p.get("compacted_count", 0)
            msg_list.add_message(ChatMessage(
                role="system",
                content=f"{_G.BULLET} compact: {count} messages",
            ))

        elif kind == "retry":
            # 重试可见性 (G3): 文案与 REPL 同源 (adapters.base.RETRY_REASON)。
            from zall.adapters.base import RETRY_REASON
            reason = RETRY_REASON.get(p.get("category", ""), p.get("category", "") or "error")
            msg_list.add_message(ChatMessage(
                role="system",
                content=(f"{_G.BULLET} retrying ({p.get('attempt', 0)}/"
                         f"{p.get('max_attempts', 0)}) in {p.get('delay', 0)}s: {reason}"),
            ))

        elif kind == "error":
            err = p.get("error", "")
            msg_list.add_message(ChatMessage(role="error", content=str(err)[:200]))

        elif kind == "runaway":
            msg_list.add_message(ChatMessage(role="error", content="runaway: step limit exceeded"))

        elif kind == "length_exceeded":
            msg_list.add_message(ChatMessage(role="error", content="length exceeded: context too long"))

        elif kind == "perception_state":
            pass

        else:
            msg_list.add_message(ChatMessage(
                role="system",
                content=f"{_G.BULLET} {kind} (step {step})",
            ))

    def _update_reactive_state(self, event: LoopEvent) -> None:
        """Update only reactive state (no widget access) when app is not mounted."""
        kind = event.kind
        p = event.payload
        step = event.step
        self._current_step = step
        self.status_step = step

        if kind == "model_call_start":
            self._is_streaming = True
            self._streaming_step = step
            self._streaming_content = ""
            self._model_calls_in_step += 1
            model_name = p.get("model", "")
            if model_name:
                self.status_model = model_name
        elif kind == "model_token":
            token = p.get("token", "")
            if token:
                self._streaming_content += token
        elif kind == "model_call":
            self._is_streaming = False
            self._update_usage(p.get("usage", {}))
        elif kind == "tool_call_end":
            self._tool_calls_in_step += 1
        elif kind == "error":
            self._is_streaming = False

    # ── UI helpers ──

    def _retry_transient(self, loop: Any, result: Any) -> Any:
        """瞬态错误(429/5xx/timeout)退避重试, 最多 3 次 — 修复"失败后中途就停"。

        返回最终 StepResult: 成功恢复则为非 terminal (调用方继续正常流程);
        重试耗尽或转为非瞬态错误则为 terminal (调用方显示错误)。
        在 worker 线程执行, sleep 不阻 UI; 状态栏 spinner 照常动。
        """
        import time as _t
        from zall._util.backoff import backoff_delay
        from zall.cli.repl_ui import is_transient_error
        _err0 = (result.egress.error if result.egress else "") or ""
        self.call_from_thread(self._show_system, f"transient error: {_err0[:80]}")
        for attempt in range(1, 4):
            if self._interrupt_requested:
                return result
            delay = round(backoff_delay(attempt), 1)  # G13: 指数+抖动退避
            self.call_from_thread(self._show_system, f"retry {attempt}/3 in {delay}s\u2026")
            _t.sleep(delay)
            try:
                result = loop.retry_step()  # 不增 step_count
            except Exception as _re:
                self.call_from_thread(self._show_error, str(_re))
                return result
            if not result.is_terminal:
                self.call_from_thread(self._show_system, "recovered, continuing")
                return result  # 成功恢复 → 继续正常流程
            if result.egress and not is_transient_error(result.egress.error):
                return result  # 非瞬态 terminal → 交给调用方显示
            # 仍是瞬态 → 继续重试
        self.call_from_thread(self._show_system,
                              "API still unavailable after 3 retries — try /provider or a faster model")
        return result

    def _show_error(self, msg: str) -> None:
        msg_list = self._get_msg_list()
        if msg_list is None:
            return
        if self._thinking_active:
            self._finalize_thinking(msg_list)
        msg_list.add_message(ChatMessage(role="error", content=str(msg)[:200]))

    def _show_interrupt(self) -> None:
        # 清空 pending 缓冲 (防 flush 回调在中断后写入陈旧内容)
        with self._buf_lock:
            self._token_buffer = ""
            self._thinking_buffer = ""
            self._token_flush_pending = False
            self._thinking_flush_pending = False
        msg_list = self._get_msg_list()
        if msg_list is not None:
            if self._thinking_active:
                self._finalize_thinking(msg_list)
            # 活跃区未完成块 → 固化 + 中断标记 (保留 partial output)
            self._flush_live(msg_list, interrupted=True)
            msg_list.add_message(ChatMessage(
                role="system",
                content=f"{_G.BULLET} interrupted",
            ))
        self._is_streaming = False
        self._streaming_content = ""

    def on_unmount(self) -> None:
        """退出时清理: 关闭 devnull sink (Bug6, 避免 fd 泄漏)。"""
        sink = getattr(self, "_null_sink", None)
        if sink is not None and sink is not sys.stderr:
            try:
                sink.close()
            except Exception:
                pass

    # ── Actions ──

    def action_interrupt(self) -> None:
        """Handle Ctrl+C: interrupt current generation."""
        # 确认门等待中被中断 → 取消 (视为 reject), 解除 worker 阻塞
        if self._confirm_active and self._confirm_responder is not None:
            self._confirm_active = False
            try:
                self._confirm_responder.cancel()
            except Exception:
                pass
        self._interrupt_requested = True
        # Also try to interrupt via the renderer
        renderer = self._state.get("_renderer")
        if renderer is not None and hasattr(renderer, "interrupt_stream"):
            renderer.interrupt_stream()
        if renderer is not None and hasattr(renderer, "_stop_spinner"):
            renderer._stop_spinner()

    def action_exit(self) -> None:
        """Handle Ctrl+D: exit the app."""
        # 确认门等待中退出 → 取消, 解除 worker 线程阻塞 (防永久挂起)
        if self._confirm_active and self._confirm_responder is not None:
            self._confirm_active = False
            try:
                self._confirm_responder.cancel()
            except Exception:
                pass
        if self._on_exit:
            self._on_exit()
        self.exit()

    def action_quit(self) -> None:
        """Handle Ctrl+Q: quit the app (alias for exit)."""
        self.action_exit()

    def action_clear_screen(self) -> None:
        """Handle Ctrl+L: clear the screen."""
        msg_list = self._get_msg_list()
        if msg_list is not None:
            msg_list.clear()
            msg_list._messages = []
        live = self._get_live()
        if live is not None:
            live.clear()

    def _show_system(self, text: str) -> None:
        """在消息区追加一条系统消息。"""
        msg_list = self._get_msg_list()
        if msg_list is not None:
            msg_list.add_message(ChatMessage(role="system", content=f"{_G.BULLET} {text}"))

    def show_confirm_request(self, tool_id: str, args: dict, level: str) -> None:
        """确认门: 在消息区显示权限请求。"""
        from rich.markup import escape as _esc
        msg_list = self._get_msg_list()
        if msg_list is None:
            return
        preview = _esc(_key_arg(args)) if args else ""
        icon = _G.WARN if level == "greylist" else _G.FAIL
        msg_list.add_message(ChatMessage(
            role="system",
            content=f"{icon} confirm {tool_id} ({level})  {preview}",
        ))

    def begin_confirm(self, prompt: str) -> None:
        """确认门: 进入确认模式, 输入框下一条提交作为权限回答。

        仅用于编辑子提示 (自由文本); 主选择走 begin_confirm_select (可选择菜单)。
        """
        self._confirm_active = True
        self._show_system("type new value \u0026 Enter (blank = keep)")
        try:
            self.query_one("#input-bar", InputBar).focus()
        except Exception:
            pass

    def begin_confirm_select(self, choices: list) -> None:
        """确认门: 弹出可选择菜单 (方向/数字键), 选中项作为权限回答。"""
        self._confirm_active = True
        try:
            input_bar = self.query_one("#input-bar", InputBar)
            input_bar.open_select("confirm tool call", list(choices))
            input_bar.focus()
        except Exception:
            # 无法弹菜单 → 直接 reject (解除 worker 阻塞)
            self._confirm_active = False
            if self._confirm_responder is not None:
                self._confirm_responder.submit_reply("n")

    def on_input_bar_select_chosen(self, event: InputBar.SelectChosen) -> None:
        """确认门选择菜单: 选中 → 作为权限回答提交。"""
        if self._confirm_active and self._confirm_responder is not None:
            self._confirm_active = False
            self._confirm_responder.submit_reply(event.value)

    def on_input_bar_select_cancelled(self, event: InputBar.SelectCancelled) -> None:
        """确认门选择菜单: Esc 取消 → reject。"""
        if self._confirm_active and self._confirm_responder is not None:
            self._confirm_active = False
            self._confirm_responder.submit_reply("n")

    def on_input_bar_interrupt(self, event: InputBar.Interrupt) -> None:
        """Esc (无菜单/选择时) → 中断当前生成 (与 Ctrl+C 同路径)。"""
        self.action_interrupt()

    def action_toggle_plan(self) -> None:
        """Shift+Tab: 切换 plan 模式 (只读探索/规划, kimi/claude parity)。

        plan_mode 是真相源: _run_agent_loop 构建 loop 时读它; 若已有 loop 则立即应用。
        """
        new_val = not self._state.get("plan_mode", False)
        self._state["plan_mode"] = new_val
        loop = self._agent_loop
        if loop is not None and hasattr(loop, "set_plan_mode"):
            try:
                loop.set_plan_mode(new_val)
            except Exception:
                pass
        # 状态栏徽章 (StatusBar 已支持 "plan" 着色) + 输入框边框模式指示 + 系统消息反馈
        self.status_mode = "plan" if new_val else ""
        self._sync_status_bar()
        try:
            self.query_one("#input-bar", InputBar).set_mode_indicator("plan" if new_val else "")
        except Exception:
            pass
        if new_val:
            self._show_system("plan mode \u2192 on (read-only: explore \u0026 plan, no writes)")
        else:
            self._show_system("plan mode \u2192 off (normal: full tool access)")

    def action_external_editor(self) -> None:
        """Ctrl+O: 用外部编辑器编辑当前输入 (kimi/claude parity)。"""
        try:
            input_bar = self.query_one("#input-bar", InputBar)
        except Exception:
            return
        editor = _detect_editor()
        if not editor:
            self._show_system("no external editor found (set $EDITOR or $VISUAL)")
            return
        import subprocess
        import tempfile
        current = input_bar._textarea.text
        tmp_path = ""
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".md", prefix="zall_input_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(current)
            with self.suspend():
                subprocess.run([*editor, tmp_path], check=False)
            with open(tmp_path, "r", encoding="utf-8") as f:
                edited = f.read().rstrip("\n")
            input_bar._textarea.text = edited
        except Exception as e:
            self._show_system(f"editor failed: {e}")
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    # ── Run ──

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Run the TUI application.

        This is a convenience wrapper that handles the textual App.run() call.
        """
        return super().run(*args, **kwargs)


# ──────────────────────────────────────────────────────────────────────────
# Factory function
# ──────────────────────────────────────────────────────────────────────────

def _detect_editor() -> list[str] | None:
    """探测外部编辑器命令 (Ctrl+O 用)。优先 $VISUAL/$EDITOR, 再按平台探测。

    返回命令前缀 list (如 ['code','--wait'] 或 ['vim']); 无可用编辑器返回 None。
    """
    import shutil
    for env in ("VISUAL", "EDITOR"):
        val = os.environ.get(env, "").strip()
        if val:
            return val.split()
    if sys.platform == "win32":
        candidates = [["code", "--wait"], ["notepad"]]
    else:
        candidates = [["code", "--wait"], ["vim"], ["vi"], ["nano"]]
    for cmd in candidates:
        if shutil.which(cmd[0]):
            return cmd
    return None


def run_tui(
    *,
    model: str | None = None,
    yes: bool = False,
    verbose: bool = False,
    strict: bool = False,
    inline: bool = False,
    resume_session: str | None = None,
) -> int:
    """Create and run the TUI application.

    This is the entry point for TUI mode. It checks terminal capabilities
    first and falls back to line-based REPL if the terminal is unsupported
    (e.g., legacy Windows ConHost, dumb terminals, CI environments).

    inline=True: 内联模式 (Textual inline) — 停靠式输入框常驻底部、模型运行时也
      可输入 (队列/steer), 但**不接管全屏** (保留滚回/复粘贴), 对齐 Claude Code / Pi。

    Returns:
        0 on success, 1 on error, 2 if TUI unsupported (caller should fallback)
    """
    # Check terminal capabilities before running TUI
    if not _check_tui_supported():
        caps = _detect_terminal_capabilities()
        import logging
        logging.getLogger("zall").warning(
            "TUI not supported on this terminal (tui=%s, unicode=%s, colors=%s, ansi=%s). "
            "Falling back to line-based REPL.",
            caps.get("tui_supported"),
            caps.get("unicode"),
            caps.get("colors"),
            caps.get("ansi"),
        )
        return 2  # Signal to caller to fallback

    # 跨平台: 终端/编码不支持 unicode → 切 ASCII 字形 (杜绝 tofu)
    if not _unicode_supported():
        use_ascii_glyphs()

    app = TuiApp(
        model=model,
        yes=yes,
        verbose=verbose,
        strict=strict,
        resume_session=resume_session,
    )
    try:
        if inline:
            # 内联渲染 (不入 alt-screen): 保留终端滚回历史, 退出不清屏
            app.run(inline=True, inline_no_clear=True)
        else:
            app.run()
        return 0
    except Exception as e:
        print(f"TUI error: {e}", file=sys.stderr)
        return 1
    finally:
        # Clean up
        try:
            from zall.cli.render import clear_console_cache
            clear_console_cache()
        except Exception:
            pass