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


def _pt_color(value: str, fallback: str) -> str:
    """prompt_toolkit 只认 hex/ANSI 名; rich 色名 (主题未 apply 时的默认值) 走 fallback。"""
    return value if (value.startswith("#") or value.startswith("ansi")) else fallback


def _build_pt_style() -> Any:
    """prompt_toolkit Style — 补全菜单/工具栏/占位符跟主题着色 (G6 单一色源)。

    之前菜单硬编码 ansiblue, 与 attic 主题两张皮; 现在菜单底色/选中项/滚动条
    都取 _C 槽位 (theme.apply 后为 hex), 未 apply 时退回 ANSI 中性色。
    """
    try:
        from prompt_toolkit.styles import Style

        from zall.cli.render import _C
        accent = _pt_color(_C.ACCENT, "ansiyellow")
        subtle = _pt_color(_C.SUBTLE, "ansibrightblack")
        text = _pt_color(_C.STATUS_BAR_TEXT, "ansiwhite")
        plan = _pt_color(_C.WARN, "ansiyellow")
        panel_bg = "#23211c"  # 深中性底 (与 attic 暖色系一致; 菜单需要不透明底)
        selected_fg = "#14120e"
        return Style.from_dict({
            "prompt": f"{accent} bold",
            "plan": f"{plan} bold",
            "placeholder": subtle,
            "bottom-toolbar": f"bg:{panel_bg} {text}",
            "completion-menu.completion": f"bg:{panel_bg} {text}",
            "completion-menu.completion.current": f"bg:{accent} {selected_fg} bold",
            "completion-menu.meta.completion": f"bg:{panel_bg} {subtle}",
            "completion-menu.meta.completion.current": f"bg:{accent} {selected_fg}",
            "scrollbar.background": f"bg:{panel_bg}",
            "scrollbar.button": f"bg:{accent}",
        })
    except Exception:
        return None


def _footer_right_segments(state: dict[str, Any]) -> list[str]:
    """footer 右侧状态段 (Codex footer 对标): 模式 · context left · cache 命中。

    - 模式: plan / fast (strict 为默认, 不占位)
    - context left: baseline-normalized (Codex 口径, 见 core.cache_stats)
    - cache: 命中率 (有缓存数据才显示, 无则省略 — 不显示"0%"误导)
    """
    right: list[str] = []
    if state.get("strict"):
        right.append("strict mode")
    if state.get("plan_mode"):
        right.append("plan mode")
    model = str(state.get("model") or "")
    ctx = int(state.get("ctx_tokens", 0) or 0)
    if ctx:
        try:
            from zall._util.model_registry import get_window_size
            from zall.core.cache_stats import context_remaining_percent
            window = int(get_window_size(model) or 0)
            pct = context_remaining_percent(ctx, window)
            if pct is not None:
                right.append(f"ctx {pct}% left / {window // 1000}k")
            else:
                right.append(f"ctx {ctx} tok")
        except Exception:
            right.append(f"ctx {ctx} tok")
    stats = state.get("cache_stats")
    try:
        if stats is not None and getattr(stats, "has_cache_data", False):
            right.append(stats.format_summary(with_write=False))
        else:
            usage = state.get("usage") or {}
            c = int(usage.get("cached", 0) or 0)
            p = int(usage.get("prompt", 0) or 0)
            if c and p:
                right.append(f"cache {round(c * 100 / p)}%")
    except Exception:
        pass
    return right


def build_footer_line(state: dict[str, Any] | None, *, width: int | None = None) -> str | None:
    """Codex 式 footer: 左侧快捷键提示, 右侧状态 (右对齐; 窄终端退化为单空格连接)。

    width=None → 不做右对齐 (纯连接, 供非 TTY/测试断言使用)。
    """
    if not state:
        return None
    model = str(state.get("model") or "zall")
    left_segs = [model, "? shortcuts", "/ commands", "@ files"]
    left = "  " + "  \u00b7  ".join(left_segs)
    right_segs = _footer_right_segments(state)
    if not right_segs:
        return left
    right = "  \u00b7  ".join(right_segs) + "  "
    if not width or width <= 0:
        return f"{left}    {right}"
    pad = width - len(left) - len(right)
    if pad < 2:
        return f"{left}  {right.strip()}"
    return f"{left}{' ' * pad}{right}"


def build_toolbar_text(state: dict[str, Any] | None) -> str | None:
    """底部状态行文本 (学 Pi/Claude/Codex): model · 键位提示 ……… ctx% left · cache。

    纯函数 (无 prompt_toolkit 依赖), 可单测。state=None/空 → None (不显 toolbar)。
    ctx_tokens 由 usage observer 实时刷新; window 查自 model_registry;
    右对齐宽度取终端列数 (取不到则不做对齐)。
    """
    if not state:
        return None
    try:
        import shutil as _shutil
        width = int(_shutil.get_terminal_size((100, 24)).columns)
    except Exception:
        width = 0
    return build_footer_line(state, width=width)


# ── 参数位补全 (Argus/cmd2 子命令+choices 补全对标) ──
# cmd2 靠 argparse 免费拿到子命令/选项补全; zall 此前只有一级命令补全。
# 这里给已知命令挂参数表: /science <sub> /science run <id> /science profile <name> …
# 候选项在 provider 内完成过滤 (供 quotable 名字等自定义匹配逻辑)。

_SCIENCE_SUBS: list[tuple[str, str]] = [
    ("new", "create a hypothesis"),
    ("list", "list hypotheses"),
    ("show", "show hypothesis + evidence"),
    ("evidence", "record evidence (supports/against)"),
    ("falsify", "falsify with a counterexample evidence"),
    ("revise", "revise a falsified hypothesis"),
    ("modules", "browse the research catalog"),
    ("use", "select a module"),
    ("set", "set an option (k=v)"),
    ("unset", "remove an option"),
    ("run", "execute module(s)"),
    ("runall", "run a whole section/tag"),
    ("last", "re-run the previous run"),
    ("report", "write REPORT.md + report.json"),
    ("profile", "research-depth preset"),
    ("fav", "favorites (add/del/run/list/clear/tag:)"),
    ("recent", "recent modules"),
    ("auto", "autonomous research loop"),
]

_FAV_SUBS: list[tuple[str, str]] = [
    ("add", "add to favorites"), ("del", "remove from favorites"),
    ("run", "run favorites"), ("list", "list favorites"),
    ("clear", "clear all"), ("tag:", "add all modules with a tag"),
]


def _prefixed(cands: list[tuple[str, str]], frag: str) -> list[tuple[str, str]]:
    fl = frag.lower()
    return [(v, d) for v, d in cands if not fl or v.lower().startswith(fl)]


def _science_module_candidates(frag: str) -> list[tuple[str, str]]:
    """模块 id 优先; 名字前缀命中时给引号名 (与 /science 的 shlex 解析兼容)。"""
    try:
        from zall.extensions.science.catalog import load_catalog
        mods = load_catalog()
    except Exception:
        return []
    fl = frag.lower()
    out: list[tuple[str, str]] = []
    for m in mods:
        if not fl or m.id.startswith(frag):
            out.append((m.id, m.name))
        elif m.name.lower().startswith(fl):
            out.append((f'"{m.name}"', m.name))
    return out


def _complete_argument(cmd: str, prev: list[str], frag: str) -> list[tuple[str, str]] | None:
    """命令参数位候选 (value, desc); None = 该命令无参数表 (回落默认行为)。"""
    if cmd == "/science":
        if not prev:
            return _prefixed(_SCIENCE_SUBS, frag)
        sub, rest = prev[0].lower(), prev[1:]
        run_flags = [("--dry-run", "preview only"), ("--timeout", "seconds")]
        if sub == "use":
            return _science_module_candidates(frag)
        if sub == "run":
            if not rest:
                return _science_module_candidates(frag) + _prefixed(run_flags, frag)
            return _prefixed(run_flags, frag)
        if sub == "runall":
            try:
                from zall.extensions.science.catalog import load_catalog
                secs = sorted({m.section for m in load_catalog()})
            except Exception:
                secs = []
            return _prefixed([(s, "section") for s in secs] + [("tag:", "by tag")], frag)
        if sub in ("set", "unset"):
            try:
                from zall.extensions.science.catalog import load_catalog
                from zall.extensions.science.state import get_science_state
                sel_id = get_science_state().selected_id
                sel = next((m for m in load_catalog() if m.id == sel_id), None)
            except Exception:
                sel = None
            opts = [(f"{o.replace('-', '_')}=", o) for o in sel.options] if sel else []
            cands = opts + (_science_module_candidates(frag) if sub == "unset" else [])
            return _prefixed(cands, frag) if cands else []
        if sub == "profile":
            try:
                from zall.extensions.science.profiles import PROFILES
                cands = [(k, ", ".join(f"{a}={b}" for a, b in sorted(v.items())))
                         for k, v in PROFILES.items()]
            except Exception:
                cands = []
            return _prefixed(cands, frag)
        if sub == "fav":
            if not rest:
                return _prefixed(_FAV_SUBS, frag)
            if rest[0] in ("add", "del", "rm", "remove"):
                return _science_module_candidates(frag)
            return []
        if sub == "modules":
            return _prefixed([("-s", "short list"), ("-d", "detailed"),
                              ("-t", "show tags"), ("tag:", "filter by tag")], frag)
        if sub == "auto":
            return _prefixed([("--budget", "max LLM calls"), ("--cycles", "max cycles"),
                              ("--module", "use a specific module as seed")], frag)
        return []
    if cmd == "/help" and not prev:
        try:
            from zall.cli.commands import get_command_meta
            return _prefixed(sorted(get_command_meta().items()), frag)
        except Exception:
            return []
    if cmd == "/model" and not prev:
        try:
            from zall._util.model_registry import _MODEL_PRESETS
            return _prefixed([(a, f"{n} · {p}") for a, n, _note, p in _MODEL_PRESETS], frag)
        except Exception:
            return []
    if cmd in ("/provider", "/prov"):
        # Argus `use N` 补全对标: provider 名 + 数字序号 + 三件套标记 (key=/base=/-p)
        try:
            from zall._util.model_registry import list_providers
            cands = [(k, f"{d} · {('env ' + str(env)) if env else 'key required'}")
                     for k, d, env, _url in list_providers()]
        except Exception:
            cands = []
        if not prev:
            numbered = [(str(i), f"{k} · {d}") for i, (k, d, _e, _u)
                        in enumerate(list_providers(), 1)] if cands else []
            return _prefixed(cands + numbered + [("-p", "persist")], frag)
        last = prev[-1].lower()
        if last.startswith("key=") or last.startswith("base="):
            return _prefixed([("model=", "model id")], frag)
        if last.startswith("model="):
            return []
        flags = [("key=", "api key (saved to [keys])"),
                 ("base=", "custom endpoint url"),
                 ("model=", "model id"), ("-p", "persist")]
        return _prefixed(flags, frag)
    if cmd == "/mode" and not prev:
        return _prefixed([("strict", "full confirm/downgrade gates"),
                          ("fast", "skip confirmation extras")], frag)
    return None


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
            # 参数位补全 (Argus/cmd2 对标): 首参之后的候选表 (子命令/模块 id/选项…)
            toks = text.split()
            if toks and toks[0].startswith("/"):
                trailing = text.endswith(" ")
                if len(toks) > 1 or trailing:
                    if trailing:
                        frag, prev = "", toks[1:]
                    else:
                        frag, prev = toks[-1], toks[1:-1]
                    cands = _complete_argument(toks[0].lower(), prev, frag)
                    if cands is not None:
                        for value, desc in cands:
                            safe_v = _html.escape(value)
                            safe_d = _html.escape(desc or "")
                            yield Completion(
                                value,
                                start_position=-len(frag),
                                display=HTML(
                                    f"<b>{safe_v}</b> <ansibrightblack>{safe_d}</ansibrightblack>"
                                ),
                                display_meta=desc or "",
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

            # 对话行配色 (console 门面纪律: 对比度要够): 提示符整行走主题
            # accent 加粗 — 模型名/箭头是用户每次落眼的锚点, 不能用暗灰。
            # 淡色只留给 placeholder (空输入提示)。[plan] 是只读警示, 单独
            # 琥珀槽位 (style 里 "plan" 类)。
            _head = prompt_text.replace("[plan]", "").replace("\u25b8", "").strip()
            _styled = HTML(
                f"<prompt>{_html.escape(_head)}</prompt> "
                + ("<plan>[plan]</plan> " if "[plan]" in prompt_text else "")
                + "<prompt>\u25b8</prompt> "
            )

            # v1.2: placeholder 提示 (借鉴 Claude Code: 空输入时显示淡色提示)。
            # 只给 REPL 主提示符 (带 ▸) — 选择器/向导等子提示各自写明要输什么
            # (如 "select [N] / search keyword:"), 再叠一句 "Type a task" 是
            # 误导 (实测: /model 选择器上挂着任务提示)。
            _placeholder = (
                HTML('<ansibrightblack>Type a task, / commands, @ files... '
                     '(Ctrl-R history, Alt-Enter multiline)</ansibrightblack>')
                if "\u25b8" in prompt_text else None
            )

            result = pt_prompt(
                _styled,
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
                style=_build_pt_style(),
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
