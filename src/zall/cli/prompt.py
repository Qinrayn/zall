"""REPL input with prompt_toolkit: slash command completion + history.

Features:
  - Slash command dropdown with descriptions (type / to see commands)
  - Fuzzy matching for command filtering
  - Arrow keys to navigate, Tab/Enter to confirm
  - Multi-line paste: Ctrl-Enter / Alt-Enter for newline, double Enter to submit
  - Keyboard shortcuts: Ctrl-L clear, Ctrl-W delete word, Ctrl-U delete line
  - Cross-session command history (~/.zall/history)

Command descriptions are sourced from the dynamic @slash_command registry
(zall.cli.commands.get_command_meta), NOT from a static dict. New commands
registered via @slash_command automatically appear in completions.

Auto-degrades to built-in input() when prompt_toolkit is unavailable.

IPR constraints:
  IPR-3: only stdlib + prompt_toolkit, no model SDK
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _home_dir() -> Path:
    """Get user home directory, robust on Windows with non-ASCII usernames."""
    home = Path.home()
    if os.name == "nt":
        userprofile = os.environ.get("USERPROFILE", "")
        if userprofile:
            try:
                alt = Path(userprofile)
                if alt.is_dir():
                    home = alt
            except Exception:
                pass
    return home


_HISTORY_FILE = _home_dir() / ".zall" / "history.jsonl"
_HISTORY_MAX = 500


def build_toolbar_text(state: dict[str, Any] | None) -> str | None:
    """组成底部状态行文本 (学 Pi/Claude): model · 上下文占用% · 模式 · 键位提示。

    纯函数 (无 prompt_toolkit 依赖), 可单测。state=None/空 → None (不显 toolbar)。
    ctx_tokens 由 usage observer 实时刷新; window 查自 model_registry。
    """
    if not state:
        return None
    model = state.get("model") or "zall"
    parts = [str(model)]
    ctx = int(state.get("ctx_tokens", 0) or 0)
    if ctx:
        try:
            from zall._util.model_registry import get_window_size
            window = int(get_window_size(str(model)) or 0)
        except Exception:
            window = 0
        if window > 0:
            parts.append(f"ctx {min(100, ctx * 100 // window)}% / {window // 1000}k")
        else:
            parts.append(f"ctx {ctx} tok")
    if state.get("plan_mode"):
        parts.append("plan")
    parts.append("/ commands \u00b7 @ files \u00b7 Ctrl-D exit")
    return "  " + "  \u00b7  ".join(parts)


def _build_custom_completer(
    commands: list[str],
    skills: list[str] | None = None,
    command_meta: dict[str, str] | None = None,
) -> Any:
    """Build a custom Completer with command descriptions in the dropdown.

    command_meta: {command_name: description} dict sourced from the dynamic
                  @slash_command registry. Falls back to get_command_meta().
    """
    try:
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.formatted_text import HTML
    except ImportError:
        return None

    import html as _html

    from zall.cli.commands import get_command_meta as _get_command_meta
    meta = command_meta if command_meta is not None else _get_command_meta()
    all_cmds = list(commands or meta.keys())
    entries: list[tuple[str, str, str]] = []
    for cmd in sorted(set(all_cmds)):
        desc = meta.get(cmd, "")
        if desc:
            entries.append((cmd, cmd, desc))
        else:
            entries.append((cmd, cmd, ""))

    if skills:
        for sk in sorted(set(skills)):
            entries.append((f"/skill {sk}", f"/skill {sk}", sk))

    class _DescCompleter(Completer):
        def get_completions(self, document: Any, complete_event: Any) -> Any:
            text = document.text_before_cursor
            if not text:
                return
            # v2.x: @ 文件路径补全 (与 TUI 一致, 共享 file_complete)。
            # 输 "@src/lo" → 候选工作区文件, 选中替换末尾 @token (保留 @)。
            from zall.cli.file_complete import file_query, workspace_file_matches
            fq = file_query(text)
            if fq is not None:
                for path in workspace_file_matches(fq, limit=10):
                    yield Completion(
                        path,
                        start_position=-len(fq),
                        display=HTML(f"<b>{_html.escape(path)}</b> <ansibrightblack>file</ansibrightblack>"),
                        display_meta="file",
                    )
                return
            text_lower = text.lower()
            for cmd, display, desc in entries:
                if cmd.lower().startswith(text_lower):
                    safe_display = _html.escape(display)
                    safe_desc = _html.escape(desc)
                    display_html = HTML(
                        f"<b>{safe_display}</b> <ansibrightblack>{safe_desc}</ansibrightblack>"
                    )
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display=display_html,
                        display_meta=desc,
                        style="bg:ansiblue fg:ansiwhite",
                    )

    return _DescCompleter()


def _build_nested_completer(commands: list[str], skills: list[str] | None = None) -> Any:
    """Fallback NestedCompleter (no descriptions, supports sub-completion)."""
    try:
        from prompt_toolkit.completion import NestedCompleter
    except ImportError:
        return None

    all_cmds = sorted(set(commands))
    nested: dict[str, Any] = {}
    for cmd in all_cmds:
        if cmd == "/skill":
            nested[cmd] = NestedCompleter.from_nested_dict(
                {sk: None for sk in (skills or [])}
            )
        else:
            nested[cmd] = None
    return NestedCompleter.from_nested_dict(nested)


def make_prompt_fn(
    prompt_str: str = "> ",
    commands: list[str] | None = None,
    skills: list[str] | None = None,
    state: dict[str, Any] | None = None,
) -> Callable[[str], str]:
    """Build an input-compatible prompt function.

    Uses prompt_toolkit when available (slash command completion, history,
    multi-line input). Falls back to built-in input() on import failure or
    when running in a non-TTY environment (pipe, CI, cmd.exe without console).

    Returns a function with signature: fn(prompt_text) -> str
    """
    # Non-TTY: skip prompt_toolkit entirely (it prints warnings in pipes)
    import sys
    if not sys.stdin.isatty():
        return lambda p: input(p)

    try:
        from prompt_toolkit import prompt as pt_prompt
        from prompt_toolkit.history import FileHistory, InMemoryHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys
    except ImportError:
        return lambda p: input(p)

    # G5: 大段粘贴折叠 (kimi placeholders 对标)。folder 是闭包级 —
    # 同一 REPL 会话内历史召回的占位符仍可展开。
    from zall.cli.paste_fold import PasteFolder
    folder = PasteFolder()

    # Prefer custom Completer (with descriptions), fall back to NestedCompleter
    from zall.cli.commands import get_command_meta as _get_command_meta
    _meta = _get_command_meta()
    completer = _build_custom_completer(
        commands or list(_meta.keys()), skills, command_meta=_meta,
    )
    if completer is None:
        completer = _build_nested_completer(
            commands or list(_meta.keys()), skills
        )

    # Persistent history (FileHistory manages automatically)
    history: Any
    try:
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        history = FileHistory(str(_HISTORY_FILE))
    except OSError:
        history = InMemoryHistory()

    # Key bindings
    bindings = KeyBindings()

    @bindings.add("c-d")
    def _exit_on_empty(event: Any) -> None:
        """Ctrl-D on empty line: exit REPL."""
        buf = event.app.current_buffer
        if not buf.text:
            event.app.exit(exception=EOFError)
        else:
            buf.delete_before_cursor(count=1)

    @bindings.add("c-l")
    def _clear_screen(event: Any) -> None:
        """Ctrl-L: clear screen."""
        event.app.renderer.clear()

    @bindings.add("c-w")
    def _delete_word(event: Any) -> None:
        """Ctrl-W: delete previous word."""
        buf = event.app.current_buffer
        text = buf.text[:buf.cursor_position]
        pos = buf.cursor_position
        i = pos - 1
        while i >= 0 and text[i] in " \t":
            i -= 1
        while i >= 0 and text[i] not in " \t":
            i -= 1
        buf.text = buf.text[:i + 1] + buf.text[pos:]
        buf.cursor_position = i + 1

    @bindings.add("c-u")
    def _delete_line(event: Any) -> None:
        """Ctrl-U: delete entire line."""
        buf = event.app.current_buffer
        buf.text = ""
        buf.cursor_position = 0

    # v1.2: Ctrl-P / Ctrl-N 历史导航 (借鉴 Claude Code / readline 标准)
    @bindings.add("c-p")
    def _history_prev(event: Any) -> None:
        """Ctrl-P: previous history entry."""
        event.app.current_buffer.history_backward()

    @bindings.add("c-n")
    def _history_next(event: Any) -> None:
        """Ctrl-N: next history entry."""
        event.app.current_buffer.history_forward()

    # v1.2: Ctrl-R 反向历史搜索 (借鉴 Claude Code / readline 标准)
    @bindings.add("c-r")
    def _reverse_search(event: Any) -> None:
        """Ctrl-R: reverse-i-search through history."""
        buf = event.app.current_buffer
        # 使用 prompt_toolkit 内置的增量搜索
        buf.start_history_search()

    @bindings.add(Keys.BracketedPaste, eager=True)
    def _bracketed_paste(event: Any) -> None:
        """G5: 粘贴作为单一事件插入 (换行不触发 Enter 绑定);
        超阈值折叠为 [Pasted text #N +M lines] 占位符。"""
        event.current_buffer.insert_text(folder.maybe_fold(event.data))

    @bindings.add("escape", "enter")
    def _newline_alt_enter(event: Any) -> None:
        """Alt-Enter: insert newline (multi-line input)."""
        buf = event.app.current_buffer
        buf.insert_text("\n")

    @bindings.add("enter")
    def _enter(event: Any) -> None:
        """Enter: submit on empty line with content, or continue multi-line."""
        buf = event.app.current_buffer
        text = buf.text.strip()

        # Empty line with multi-line content: submit (double Enter)
        if not text and "\n" in buf.text:
            event.app.exit(result=buf.text.rstrip("\n"))
            return

        # Line ends with backslash: continuation
        if buf.text.rstrip().endswith("\\"):
            buf.text = buf.text.rstrip()[:-1] + "\n"
            buf.cursor_position = len(buf.text)
            return

        # Unclosed parentheses: continuation
        full_text = buf.text
        open_parens = full_text.count("(") + full_text.count("[") + full_text.count("{")
        close_parens = full_text.count(")") + full_text.count("]") + full_text.count("}")
        if open_parens > close_parens and text:
            buf.insert_text("\n")
            return

        # Normal submit
        event.app.exit(result=buf.text)

    def _bottom_toolbar() -> Any:
        """持久底部状态行 (学 Pi/Claude)。委托 build_toolbar_text (可单测), 包 HTML 转义。"""
        txt = build_toolbar_text(state)
        if txt is None:
            return None
        try:
            import html as _h

            from prompt_toolkit.formatted_text import HTML
            return HTML(_h.escape(txt))
        except Exception:
            return txt

    def _input_fn(prompt_text: str) -> str:
        """prompt_toolkit input with slash completion + history + multi-line."""
        try:
            import html as _html

            from prompt_toolkit.formatted_text import HTML
            from prompt_toolkit.shortcuts import CompleteStyle

            styled_prompt = HTML(f"<ansibrightblack>{_html.escape(prompt_text)}</ansibrightblack>")

            # v1.2: placeholder 提示 (借鉴 Claude Code: 空输入时显示淡色提示)
            _placeholder = HTML(
                '<ansibrightblack>Type a task, / commands, @ files... '
                '(Ctrl-R history, Alt-Enter multiline)</ansibrightblack>'
            )

            result = pt_prompt(
                styled_prompt,
                completer=completer,
                history=history,
                key_bindings=bindings,
                complete_while_typing=True,
                reserve_space_for_menu=8,
                complete_style=CompleteStyle.MULTI_COLUMN,
                multiline=True,
                vi_mode=False,
                placeholder=_placeholder,
                bottom_toolbar=_bottom_toolbar,
            )
            # G5: 提交时展开粘贴占位符 (发给模型的是原文);
            # 未知 id (跨会话历史召回) 原样保留。
            return folder.expand(result)
        except (EOFError, KeyboardInterrupt):
            raise
        except Exception as e:
            # prompt_toolkit failure: warn and fall back to built-in input()
            import sys as _sys
            _sys.stderr.write(f"  [prompt] {e}\n")
            _sys.stderr.flush()
            return input(prompt_text)

    return _input_fn
