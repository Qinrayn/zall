"""TUI widgets for the zall full-screen terminal UI.

Widgets:
  - MessageList: scrollable message history (RichLog-based)
  - InputBar: multi-line input with TextArea, history, and placeholder
  - StatusBar: model name, directory, git branch, step count, token usage, permission mode
  - ToolPanel: collapsible tool call panel with diff support
  - ThinkingPanel: collapsible thinking/reasoning panel

Design (inspired by Claude Code):
  - Multi-line input via TextArea with history navigation (up/down arrows)
  - Syntax-highlighted code blocks in messages
  - Color-coded tool types (read=blue, write=gold, bash=orange)
  - Diff rendering for edit_file results
  - Status bar with model/dir/branch/steps/tokens/mode
"""

from __future__ import annotations

import re
import time
from typing import Any

from rich.console import Group
from rich.markdown import Markdown
from rich.markup import escape as _markup_escape
from rich.panel import Panel
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import RichLog, TextArea

from zall._util.string import shorten  # G11: cell-width 截断
from zall.cli import render as _render_mod  # G6: CODE_THEME/CODE_BG 随主题变, 须模块属性访问
from zall.cli.paste_fold import PasteFolder  # G5: 大段粘贴折叠
from zall.cli.render import _C, _G, _display_tool_name, _key_arg
from zall.cli.syntax_theme import (  # G7: ANSI-16 语法主题解析
    resolve_code_bg as _resolve_code_bg,
)
from zall.cli.syntax_theme import (
    resolve_code_theme as _resolve_code_theme,
)

# 选择菜单 type-ahead 宽限期 (秒): 打开后此期限内的决策键 (Enter/数字) 被吞。
# 滞留键重放几乎瞬时 (<50ms); 真人看到菜单再决策至少需要 ~300ms — 0.35s
# 既拦住所有滞留键, 又不让真人感知到延迟。
SELECT_GRACE_S = 0.35

_DECISION_KEYS = frozenset({"1", "2", "3", "4", "5", "6", "7", "8", "9", "enter"})


def is_typeahead_decision(key: str, opened_at: float, now: float) -> bool:
    """选择菜单 type-ahead 判定: 宽限期内的决策键应被吞掉。

    只拦决策键 (Enter/数字 — 会立即批准/选中); 导航 (↑↓) 与 Esc 不拦:
    误导航无害, 误 Esc 是安全方向 (reject)。opened_at=0 视为未记录 (不拦)。
    """
    return (key in _DECISION_KEYS
            and opened_at > 0
            and (now - opened_at) < SELECT_GRACE_S)


def _estimate_tokens(text: str) -> int:
    """粗略 token 估算 (CJK ~1.5/字, 其余 ~1 token/4 字符)。供思考行 token 提示。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return int(cjk * 1.5 + other / 4)


def _think_frame(elapsed: float) -> str:
    """根据已耗时选当前动画帧 (思考行心跳指示)。"""
    frames = _G.SPINNER_FRAMES
    return frames[int(elapsed / 0.15) % len(frames)]


def _render_diff_panel(diff_text: str, *, path: str = "") -> Any:
    """unified diff → 结构化 diff 面板 (G1: cli/diff_render 三形态)。

    优先走 diff_render (行号列 + 背景色 + 词级高亮); 解析失败回退旧版文本着色。
    """
    try:
        from zall.cli import diff_render as _dr
        hunks, truncated = _dr.parse_unified_hunks(diff_text)
        if hunks:
            return _dr.render_diff_panel(path, hunks, truncated=truncated)
    except Exception:
        pass
    return _render_diff_panel_legacy(diff_text, path=path)


def _render_diff_panel_legacy(diff_text: str, *, path: str = "") -> Any:
    """旧版文本着色回退 (diff_render 解析失败时)。box.SQUARE 通用无 tofu。"""
    from rich import box
    from rich.panel import Panel
    lines = diff_text.split("\n")
    MAX = 40
    styled: list[Text] = []
    cur = 0            # 当前新文件行号 (从 @@ 解析)
    added = removed = 0
    for ln in lines[:MAX]:
        ln = ln.rstrip("\r")
        if ln.startswith("@@"):
            m = re.search(r"\+(\d+)", ln)
            if m:
                cur = int(m.group(1))
            styled.append(Text(ln, style=_C.INFO))
        elif ln.startswith("+++") or ln.startswith("---"):
            continue   # 跳过文件头
        elif ln.startswith("+"):
            styled.append(Text(f"{cur:>4} + {ln[1:]}", style="green"))
            cur += 1
            added += 1
        elif ln.startswith("-"):
            styled.append(Text(f"     - {ln[1:]}", style="red"))
            removed += 1
        else:
            styled.append(Text(f"{cur:>4}   {ln}", style=_C.SUBTLE))
            cur += 1
    if len(lines) > MAX:
        styled.append(Text(f"     {_G.ELLIPSIS} (+{len(lines) - MAX} more lines)", style=_C.SUBTLE))
    title = f"{path}  +{added} -{removed}" if path else f"diff  +{added} -{removed}"
    body = Text("\n").join(styled) if styled else Text("(no changes)")
    return Panel(body, title=title, title_align="left",
                 border_style=_C.SUBTLE, box=box.SQUARE, padding=(0, 1), expand=False)


# ──────────────────────────────────────────────────────────────────────────
# Message types for the chat history
# ──────────────────────────────────────────────────────────────────────────

class ChatMessage:
    """A single message in the chat history."""
    __test__ = False

    def __init__(
        self,
        role: str,
        content: str = "",
        *,
        tool_id: str | None = None,
        tool_args: dict[str, Any] | None = None,
        tool_success: bool | None = None,
        tool_output: str | None = None,
        reasoning: str | None = None,
        token_count: int | None = None,
        elapsed: float | None = None,
        thinking: str | None = None,
        streaming: bool = False,
    ) -> None:
        self.role = role  # "user", "assistant", "tool", "thinking", "system", "error"
        self.content = content
        self.tool_id = tool_id
        self.tool_args = tool_args or {}
        self.tool_success = tool_success
        self.tool_output = tool_output
        self.reasoning = reasoning
        self.token_count = token_count
        self.elapsed = elapsed
        self.thinking = thinking
        self.thinking_streaming = False  # v2.x: True=思考中(静态指示), False=完成(dim 块)
        self.timestamp = time.time()
        self.streaming = streaming
        self.streaming_content = ""
        self._streaming_content: str = ""
        self._rich_cache: Any = None  # 性能: 定格消息的 to_rich 缓存 (避免重复高亮)

    def _render_code_block(self, code: str, lang: str | None = None) -> Any:
        """无边框代码块 (Claude/Codex 风格): dim 语言标签 + 主题化 Syntax。

        去掉 grey 边框 Panel 与 monokai (与暗色主题割裂); 短块 (<=20 行) 不显行号 → 更轻。
        pygments 不可用/语言未知时回退纯文本。
        """
        code = code.rstrip("\n")
        try:
            line_count = code.count("\n") + 1
            syntax = Syntax(
                code,
                (lang.strip() if (lang and lang.strip()) else "text"),
                theme=_resolve_code_theme(_render_mod.CODE_THEME),  # G7: zall-ansi → 实例
                line_numbers=line_count > 20,
                word_wrap=True,
                padding=(0, 2),
                background_color=_resolve_code_bg(_render_mod.CODE_BG),  # G7: 空 = 跟随终端
            )
            if lang and lang.strip():
                return Group(Text(f"  {lang.strip()}", style=_C.DIM), syntax)
            return Group(syntax)
        except Exception:
            return Text(code, style="grey74")

    def _render_markdown_with_syntax_highlighting(self, content: str) -> list[Any]:
        """Render markdown content with syntax-highlighted code blocks.
        
        Parses code blocks (```lang ... ```) and renders them with
        rich.syntax.Syntax, while passing the rest through rich.markdown.Markdown.
        
        This is a hybrid approach: we extract code blocks, render them
        with Syntax, and use Markdown for the rest. Claude Code uses a
        custom StreamingMarkdown component for the same purpose.
        """
        parts: list[Any] = []
        
        # Split on code blocks
        pattern = r"```(\w*)\n(.*?)```"
        last_end = 0
        
        for match in re.finditer(pattern, content, re.DOTALL):
            # Text before this code block
            before = content[last_end:match.start()]
            if before.strip():
                try:
                    parts.append(Markdown(before, code_theme=_resolve_code_theme(_render_mod.CODE_THEME)))
                except Exception:
                    parts.append(Text(before.strip(), style="grey74"))
            
            # The code block
            lang = match.group(1) or None
            code = match.group(2)
            parts.append(self._render_code_block(code, lang))
            last_end = match.end()
        
        # Remaining text after last code block
        remaining = content[last_end:]
        if remaining.strip():
            try:
                parts.append(Markdown(remaining, code_theme=_resolve_code_theme(_render_mod.CODE_THEME)))
            except Exception:
                parts.append(Text(remaining.strip(), style="grey74"))
        
        return parts if parts else [Markdown(content, code_theme=_resolve_code_theme(_render_mod.CODE_THEME)) if content.strip() else Text("")]

    def to_rich(self) -> Any:
        """Convert to a rich renderable for display."""
        if self.role == "user":
            # Claude/Pi 式: 输入提示符 ❯ + 明亮正文 (比整行 gold 更克制)
            out = Text()
            out.append("\u276f ", style=Style(color=_C.ACCENT, bold=True))
            out.append(self.content, style=Style(bold=True))
            return out
        elif self.role == "assistant":
            display_content = self._streaming_content or self.streaming_content or self.content
            if display_content:
                # Check if content has code blocks
                if "```" in display_content:
                    parts = self._render_markdown_with_syntax_highlighting(display_content)
                    # If there's only one part, return it directly
                    if len(parts) == 1:
                        return parts[0]
                    # Otherwise, wrap all parts in a Group
                    return Group(*parts)
                return Markdown(display_content, code_theme=_resolve_code_theme(_render_mod.CODE_THEME))
            return Text("(empty response)", style="grey50")
        elif self.role == "tool":
            # kimi 式: ● Using/Used name (args) — 状态圆点 + 动词 + 蓝名 + 灰参, └ dim 输出
            name = _display_tool_name(self.tool_id or "tool")
            ok = self.tool_success
            running = ok is None
            dot_color = _C.SELECT if running else (_C.SUCCESS if ok else _C.FAIL)
            verb = "Using " if running else "Used "
            preview = _key_arg(self.tool_args)
            out = Text()
            out.append("\u25cf ", style=dot_color)               # ● 状态圆点 (活跃蓝/成绿/败红)
            out.append(verb, style=_C.DIM)                       # Using / Used
            out.append(name, style=f"bold {_C.INFO}")            # 工具名 (kimi 蓝)
            if preview:
                out.append(" (", style=_C.DIM)
                out.append(preview, style=_C.DIM)
                out.append(")", style=_C.DIM)
            if self.tool_output:
                lines = self.tool_output.strip().split("\n")
                is_diff = (self.tool_id in ("edit_file", "batch_edit")
                           and any(ln[:1] in "+-@" for ln in lines[:20]))
                if is_diff:
                    # kimi 式: 完整 diff 面板 (带 +/- 行号、边框、文件名标题)
                    path = str(self.tool_args.get("path") or self.tool_args.get("file_path", ""))
                    return Group(out, _render_diff_panel(self.tool_output, path=path))
                # 非 diff: └ 缩进 dim 输出预览
                MAX_PREVIEW = 6
                display_lines = lines[:MAX_PREVIEW]
                for i, ln in enumerate(display_lines):
                    ln = ln.rstrip("\r")
                    conn = f"  {_G.DEPTH_END} " if i == 0 else "    "   # 首行用└ 连接符, 余行对齐
                    out.append(f"\n{conn}{ln}", style=_C.DIM)
                if len(lines) > MAX_PREVIEW:
                    out.append(f"\n    {_G.ELLIPSIS} (+{len(lines) - MAX_PREVIEW} more lines)", style=_C.SUBTLE)
            return out
        elif self.role == "thinking":
            # kimi 式极简思考: 单行动画 (不刷屏推理正文) — 流式中显“Thinking … Ns · N tokens”,
            # 完成后定格为“Thought for Ns · N tokens” (grey italic)。推理正文仍存于 timeline。
            body = (self.thinking or "").strip()
            elapsed = max(0.0, time.time() - self.timestamp)
            approx_tok = _estimate_tokens(body)
            if self.thinking_streaming:
                out = Text()
                out.append("Thinking", style=f"italic {_C.THINKING}")
                out.append(f" {_think_frame(elapsed)}", style=_C.THINKING)
                if elapsed >= 0.5:
                    out.append(f"  {elapsed:.1f}s", style=_C.DIM)
                if approx_tok > 0:
                    out.append(f" \u00b7 {approx_tok} tokens", style=_C.DIM)
                return out
            if not body:
                return Text.from_markup(f"[italic {_C.DIM}]Thought[/]")
            label = f"Thought for {elapsed:.1f}s" if elapsed >= 0.5 else "Thought"
            if approx_tok > 0:
                label += f" \u00b7 {approx_tok} tokens"
            return Text(label, style=f"italic {_C.DIM}")
        elif self.role == "error":
            return Text.from_markup(f"[{_C.FAIL}]{_G.FAIL} {self.content}[/]")
        elif self.role == "system":
            return Text.from_markup(f"[{_C.SUBTLE}]{_G.BULLET} {self.content}[/]")
        return Text(self.content)

    def to_rich_cached(self) -> Any:
        """定格消息的缓存渲染 (流式消息绕过缓存)。

        性能: _rerender_all 每次流式节流都重建整个消息列表; 对已完成消息
        重复做 markdown/语法高亮是长会话 O(n×高亮) 卡顿源; 缓存后只重算流式那一条。
        """
        if self.streaming or self.thinking_streaming:
            return self.to_rich()
        if self._rich_cache is None:
            self._rich_cache = self.to_rich()
        return self._rich_cache


# ──────────────────────────────────────────────────────────────────────────
# MessageList widget
# ──────────────────────────────────────────────────────────────────────────

class MessageList(RichLog):
    """Scrollable message history widget.

    Displays chat messages with rich formatting.
    Auto-scrolls to bottom on new messages.
    """

    __test__ = False

    def __init__(self, *, max_lines: int = 10_000, **kwargs: Any) -> None:
        super().__init__(max_lines=max_lines, markup=True, auto_scroll=True, **kwargs)
        self._messages: list[ChatMessage] = []

    def add_message(self, msg: ChatMessage) -> None:
        """Add a message to the list and render it."""
        self._messages.append(msg)
        renderable = msg.to_rich()
        if renderable:
            # Write a blank line separator before non-system messages
            if msg.role not in ("system", "thinking"):
                self.write(Text(""))
            self.write(renderable)

    def update_last_message(self, content: str) -> None:
        """Update the last assistant message's streaming content (append-only, no rerender).

        v3.x (kimi 固化范式): 流式内容现在走 LiveRegion, 不再整树重绘。
        保留本方法仅供兼容 (设置 _streaming_content, 不触发 clear+rewrite)。
        """
        if self._messages and self._messages[-1].role == "assistant":
            self._messages[-1]._streaming_content = content

    @property
    def last_message(self) -> ChatMessage | None:
        return self._messages[-1] if self._messages else None


# ───────────────────────────────────────────────────────────────
# LiveRegion — kimi 流式固化范式的活跃区 (v3.x)
# ───────────────────────────────────────────────────────────────

class LiveRegion(Widget):
    """“进行中”单块活跃区 (借鉴 kimi rich.Live 固化范式)。

    kimi “现代感”的真正来源: 只有正在流式/执行的单块在活跃区跳动,
    完成即由 App flush 到 MessageList 历史并 clear。相比旧的每 80ms clear()+整树
    重写 (O(n²)+闪烁), 本 widget 只持一个块、refresh() 为 O(1)。

    持一个可选 ChatMessage (复用其 to_rich 渲染 → 活跃块与固化后历史块观感一致)。
    """

    __test__ = False

    DEFAULT_CSS = """
    LiveRegion {
        height: auto;
        margin: 0 1;
        padding: 0 2;
        background: $background;
    }
    LiveRegion.hidden {
        display: none;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._msg: ChatMessage | None = None
        self.add_class("hidden")

    @property
    def is_active(self) -> bool:
        return self._msg is not None

    @property
    def message(self) -> ChatMessage | None:
        return self._msg

    def set_message(self, msg: ChatMessage | None) -> None:
        """设为当前活跃块 (None → 隐藏)。"""
        self._msg = msg
        if msg is None:
            self.add_class("hidden")
        else:
            self.remove_class("hidden")
        self.refresh()

    def clear(self) -> None:
        """清空活跃区 (块已固化到历史后调用)。"""
        self.set_message(None)

    def render(self) -> Any:
        if self._msg is None:
            return Text("")
        return self._msg.to_rich()


# ──────────────────────────────────────────────────────────────────────────
# CommandMenu — 斜杠命令下拉菜单 (v1.8, 学 kimi/opencode)
# ──────────────────────────────────────────────────────────────────────────

class CommandMenu(Widget):
    """斜杠命令下拉菜单: 输入 / 时弹出, 显示匹配命令 + 描述。

    v1.8: 学 kimi/opencode 的命令发现性 — 用户输 / 即见所有命令,
    Tab 补全, 上下键选择, Enter 执行。无 emoji, 纯几何符号 + 颜色层次。
    """

    __test__ = False

    DEFAULT_CSS = """
    CommandMenu {
        height: auto;
        background: $surface;
        border: solid $panel;
        padding: 0 1;
    }
    CommandMenu.hidden {
        display: none;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._items: list[tuple[str, str]] = []  # (名称, 描述)
        self._selected: int = 0
        self._offset: int = 0    # 滚动窗口起点 (只显 _WINDOW 条, 滚动可看全 — 不再截断丢失)
        self._prefix: str = "/"  # v2.x: 渲染前缀 (命令=/ , 文件=空)
        self._hint: str = ""     # 底部操作提示行 (dim)
        self.add_class("hidden")

    _WINDOW = 10  # 可见行数 (超出滚动, 不再 [:8] 截断丢失)

    @property
    def visible_menu(self) -> bool:
        return not self.has_class("hidden") and bool(self._items)

    @property
    def selected_command(self) -> str | None:
        if self._items:
            return self._items[self._selected][0]
        return None

    def update_items(self, items: list[tuple[str, str]], prefix: str = "/", *, hint: str = "") -> None:
        self._items = list(items)   # 保留全部 (滚动可见, 不再 [:8] 丢弃)
        self._prefix = prefix
        self._hint = hint
        self._selected = 0
        self._offset = 0
        if self._items:
            self.remove_class("hidden")
        else:
            self.add_class("hidden")
        self.refresh()

    def move_selection(self, delta: int) -> None:
        if not self._items:
            return
        n = len(self._items)
        self._selected = (self._selected + delta) % n
        # 滚动窗口使选中项保持可见 (含 wrap 边界)
        if self._selected < self._offset:
            self._offset = self._selected
        elif self._selected >= self._offset + self._WINDOW:
            self._offset = self._selected - self._WINDOW + 1
        self._offset = max(0, min(self._offset, max(0, n - self._WINDOW)))
        self.refresh()

    def hide(self) -> None:
        self.add_class("hidden")
        self._items = []
        self.refresh()

    def render(self) -> Text:
        if not self._items:
            return Text("")
        n = len(self._items)
        start = self._offset
        end = min(start + self._WINDOW, n)
        visible = self._items[start:end]
        out = Text()
        # 计算命令名列宽 (对齐描述) — 仅按可见项
        name_width = max(len(nm) for nm, _ in visible) + 2
        for idx, (name, desc) in enumerate(visible):
            i = start + idx
            out.append("\n" if idx else "", style="")
            if i == self._selected:
                out.append(f" {_G.TOOL} ", style=f"bold {_C.ACCENT}")
                out.append(f"{self._prefix}{name}", style=f"bold {_C.ACCENT}")
                out.append(" " * (name_width - len(name)), style="")
                out.append(f" {desc}", style=_C.STATUS_BAR_TEXT)
            else:
                out.append("   ", style="")
                out.append(f"{self._prefix}{name}", style=_C.INFO)
                out.append(" " * (name_width - len(name)), style="")
                out.append(f" {desc}", style=_C.DIM)
        # 有隐藏项时显位置 (k/n), 告知用户还有更多 (修 “显示不完全”)
        tail = self._hint
        if n > self._WINDOW:
            pos = f"{self._selected + 1}/{n}"
            tail = f"{self._hint}  \u00b7  {pos}" if self._hint else pos
        if tail:
            out.append(f"\n {tail}", style=_C.SUBTLE)
        return out


class SelectMenu(Widget):
    """通用选择菜单: 标题 + 编号选项 (↑↓ 导航, 1-9 直选, Enter 确认, Esc 取消)。

    供确认门 / 选型 / 会话选择器复用。走 zall 主题, 无 emoji。选项: (value, label, desc)。
    """

    __test__ = False

    DEFAULT_CSS = """
    SelectMenu {
        height: auto;
        background: $surface;
        border: solid $accent;
        padding: 0 1;
    }
    SelectMenu.hidden {
        display: none;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._title: str = ""
        self._choices: list[tuple[str, str, str]] = []  # (value, label, desc)
        self._selected: int = 0
        self._hint: str = ""
        self.add_class("hidden")

    @property
    def is_open(self) -> bool:
        return not self.has_class("hidden") and bool(self._choices)

    @property
    def selected_value(self) -> str | None:
        return self._choices[self._selected][0] if self._choices else None

    def open(self, title: str, choices: list[tuple[str, str, str]], *, hint: str = "") -> None:
        self._title = title
        self._choices = list(choices)[:9]  # 数字键 1-9
        self._selected = 0
        self._hint = hint or "\u2191\u2193 move \u00b7 1-9 pick \u00b7 Enter select \u00b7 Esc cancel"
        self.remove_class("hidden")
        self.refresh()

    def close(self) -> None:
        self.add_class("hidden")
        self._choices = []
        self.refresh()

    def move(self, delta: int) -> None:
        if self._choices:
            self._selected = (self._selected + delta) % len(self._choices)
            self.refresh()

    def select_number(self, n: int) -> bool:
        """n 为 1-based; 命中则设为选中并返回 True。"""
        if 1 <= n <= len(self._choices):
            self._selected = n - 1
            self.refresh()
            return True
        return False

    def render(self) -> Text:
        if not self._choices:
            return Text("")
        out = Text()
        if self._title:
            out.append(self._title, style=f"bold {_C.INFO}")
        for i, (_value, label, desc) in enumerate(self._choices):
            out.append("\n")
            num = i + 1
            if i == self._selected:
                # kimi 式选中行: → [N] label (箭头 + 方括号编号, amber 品牌高亮)
                out.append(f" \u2192 [{num}] {label}", style=f"bold {_C.ACCENT}")
                if desc:
                    out.append(f"  {desc}", style=_C.STATUS_BAR_TEXT)
            else:
                out.append(f"   [{num}] {label}", style=_C.INFO)
                if desc:
                    out.append(f"  {desc}", style=_C.DIM)
        if self._hint:
            out.append(f"\n {self._hint}", style=_C.SUBTLE)
        return out


# ──────────────────────────────────────────────────────────────────────────
# ChatTextArea — 修复 Enter 发送的关键 (v1.7)
# ──────────────────────────────────────────────────────────────────────────

class ChatTextArea(TextArea):
    """TextArea 子类: Enter 发送 / Shift+Enter 换行 / 边界处上下键导航历史。

    v1.7 修复致命 BUG: 原生 TextArea 对 enter 调 event.stop() 并插入换行,
    导致父组件 on_key 永远收不到 enter — Enter 只能换行、发不了消息。
    且 textual 8.x 的 TextArea 根本没有 Submitted 消息 (旧代码引用了不存在的属性)。
    正确做法: 子类化 _on_key, 在 TextArea 默认处理前拦截。
    """

    __test__ = False

    class Submitted(Message):
        """用户按 Enter 提交。"""
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class HistoryPrev(Message):
        """光标在第一行时按上键 — 请求上一条历史。"""

    class HistoryNext(Message):
        """光标在最后一行时按下键 — 请求下一条历史。"""

    class Interrupt(Message):
        """Esc (无菜单时) — 请求中断当前生成 (kimi/Claude parity)。"""

    # v1.8: 菜单模式消息 (命令菜单打开时)
    class MenuPrev(Message):
        """菜单打开时按上键 — 上一项。"""

    class MenuNext(Message):
        """菜单打开时按下键 — 下一项。"""

    class MenuComplete(Message):
        """菜单打开时按 Tab — 补全选中命令。"""

    class MenuDismiss(Message):
        """菜单打开时按 Esc — 关闭菜单。"""

    # 选择菜单模式消息 (确认门/选型/会话选择器打开时)
    class SelectPrev(Message):
        """选择菜单: 上一项。"""

    class SelectNext(Message):
        """选择菜单: 下一项。"""

    class SelectNum(Message):
        """选择菜单: 数字键直选 (1-based)。"""
        def __init__(self, n: int) -> None:
            super().__init__()
            self.n = n

    class SelectConfirm(Message):
        """选择菜单: Enter 确认当前项。"""

    class SelectCancel(Message):
        """选择菜单: Esc 取消。"""

    # v2.x (kimi parity): 交互模式快捷键
    class TogglePlan(Message):
        """Shift+Tab — 请求切换 plan 模式。"""

    class OpenEditor(Message):
        """Ctrl+O — 请求用外部编辑器编辑当前输入。"""

    class Steer(Message):
        """Ctrl+S — 请求将当前输入注入正在运行的回合 (steer)。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # v1.8: 菜单模式标志 (由 InputBar 根据输入内容设置)
        self.menu_mode: bool = False
        # 选择菜单模式 (确认门/选型/会话; 完全接管键盘, 禁自由输入)
        self.select_mode: bool = False
        # type-ahead 防护 (kimi 审批面板思想): 菜单打开前滞留在消息泵里的
        # Enter/数字键不得立即成为决策 — 开启后短宽限期内决策键一律吞掉
        # (导航键不受限)。防止模型运行时用户提前敲的回车误批写盘操作。
        self.select_opened_at: float = 0.0
        # G5: 大段粘贴折叠 (kimi placeholders 对标) — 占位符在提交时展开
        self._paste_folder = PasteFolder()

    @property
    def expanded_text(self) -> str:
        """当前文本, 粘贴占位符展开为原文 (提交/steer 用)。"""
        return self._paste_folder.expand(self.text)

    async def _on_paste(self, event: events.Paste) -> None:
        """G5: 大段粘贴折叠为 [Pasted text #N +M lines] 占位符。

        小段粘贴也经此处归一化 CRLF (TextArea 内部用 LF)。
        选择菜单模式下禁输入, 粘贴直接吞掉。
        """
        event.stop()
        event.prevent_default()
        if self.select_mode or self.read_only:
            return
        folded = self._paste_folder.maybe_fold(event.text)
        start, end = self.selection
        self._replace_via_keyboard(folded, start, end)

    async def _on_key(self, event: events.Key) -> None:
        # 选择菜单模式 (确认门/选型/会话): 完全接管键盘, 不允许自由输入
        if self.select_mode:
            k = event.key
            # type-ahead 防护: 菜单刚打开的宽限期内, 决策键 (Enter/数字) 吞掉 —
            # 它们几乎必然是菜单弹出前用户就已敲下的滞留键, 不代表对菜单的决策。
            # 导航 (↑↓) / Esc 不受限: 误导航无害, 误 Esc 是安全方向 (reject)。
            if is_typeahead_decision(k, self.select_opened_at, time.monotonic()):
                event.stop(); event.prevent_default(); return
            if k == "up":
                event.stop(); event.prevent_default()
                self.post_message(self.SelectPrev()); return
            if k == "down":
                event.stop(); event.prevent_default()
                self.post_message(self.SelectNext()); return
            if k in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
                event.stop(); event.prevent_default()
                self.post_message(self.SelectNum(int(k))); return
            if k == "enter":
                event.stop(); event.prevent_default()
                self.post_message(self.SelectConfirm()); return
            if k == "escape":
                event.stop(); event.prevent_default()
                self.post_message(self.SelectCancel()); return
            # 其它键在选择模式下吞掉 (禁自由输入)
            event.stop(); event.prevent_default(); return
        # textual 8.x: Shift+Enter 的 event.key 是 "shift+enter" (无 .shift 属性)
        # v2.x (kimi parity): Shift+Tab 切 plan / Ctrl+O 外部编辑器 — 最高优先, 菜单开时也生效
        if event.key == "shift+tab":
            event.stop(); event.prevent_default()
            self.post_message(self.TogglePlan()); return
        if event.key == "ctrl+o":
            event.stop(); event.prevent_default()
            self.post_message(self.OpenEditor()); return
        if event.key == "ctrl+s":
            event.stop(); event.prevent_default()
            self.post_message(self.Steer()); return
        # v1.8: 菜单模式 — 上下键导航菜单, Tab 补全, Esc 关闭
        if self.menu_mode:
            if event.key == "up":
                event.stop(); event.prevent_default()
                self.post_message(self.MenuPrev()); return
            if event.key == "down":
                event.stop(); event.prevent_default()
                self.post_message(self.MenuNext()); return
            if event.key == "tab":
                event.stop(); event.prevent_default()
                self.post_message(self.MenuComplete()); return
            if event.key == "escape":
                event.stop(); event.prevent_default()
                self.post_message(self.MenuDismiss()); return
            # 菜单模式下 Enter 仍走提交 (InputBar 会先补全选中项)
        # Esc (无菜单/选择时): 中断当前生成
        if event.key == "escape":
            event.stop(); event.prevent_default()
            self.post_message(self.Interrupt()); return
        # Enter (无 shift): 提交, 不插换行 (G5: 占位符展开为原文)
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.expanded_text))
            return
        # Shift+Enter: 插入换行 (原生 TextArea 不处理 shift+enter)
        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return
        # 上键 + 光标在第一行: 导航历史 (否则交给 TextArea 移动光标)
        if event.key == "up" and self.cursor_location[0] == 0:
            event.stop()
            event.prevent_default()
            self.post_message(self.HistoryPrev())
            return
        # 下键 + 光标在最后一行: 导航历史
        if event.key == "down" and self.cursor_location[0] == self.document.line_count - 1:
            event.stop()
            event.prevent_default()
            self.post_message(self.HistoryNext())
            return
        # 其他键: TextArea 默认行为
        await super()._on_key(event)


# ──────────────────────────────────────────────────────────────────────────
# InputBar widget — enhanced with ChatTextArea, history, and better placeholder
# ──────────────────────────────────────────────────────────────────────────

class InputBar(Widget):
    """Multi-line input bar with ChatTextArea, history navigation, and placeholder.

    Inspired by Claude Code's PromptInput:
      - ChatTextArea for proper multi-line editing (cursor, selection, undo/redo)
      - Enter to submit, Shift+Enter for newline (v1.7: 修复 Enter 只换行的 BUG)
      - Up/Down arrow keys for input history navigation (at text boundaries)
      - Placeholder text showing available commands
      - Visual focus indicator matching Claude Code's amber accent
    """

    __test__ = False

    class Submitted(Message):
        """Posted when the user submits a message."""
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    # v2.x (kimi parity): 向上重新投递的交互请求 (供 App 处理)
    class TogglePlan(Message):
        """Shift+Tab plan-mode toggle request."""

    class OpenEditor(Message):
        """Ctrl+O external-editor request."""

    class Steer(Message):
        """Ctrl+S steer request (carries current input text)."""
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class SelectChosen(Message):
        """选择菜单确认: 携带选中项的 value。"""
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    class SelectCancelled(Message):
        """选择菜单取消 (Esc)。"""

    class Interrupt(Message):
        """Esc 中断请求 (向 App 重投)。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._history: list[str] = []
        self._history_index: int = -1
        self._current_draft: str = ""
        self._textarea = ChatTextArea(
            text="",
            soft_wrap=True,
            placeholder="Ask zall  \u00b7  / commands  \u00b7  @ files  \u00b7  Shift+Tab plan",
        )
        # kimi 式输入标识: 边框标题显提示符/模式 (默认 normal)
        self._textarea.border_title = "\u276f zall"
        # v1.8: 斜杠命令菜单 (v2.x: 兼作 @ 文件补全)
        self._menu = CommandMenu()
        self._menu_kind: str = "command"  # "command" | "file"
        self._file_cache: list[str] | None = None  # @ 补全: 工作区文件缓存 (惰性建)
        # 通用选择菜单 (确认门/选型/会话选择器)
        self._select = SelectMenu()

    def compose(self) -> ComposeResult:
        yield self._select
        yield self._menu
        yield self._textarea

    def set_mode_indicator(self, mode: str = "") -> None:
        """更新输入框边框标题的模式指示 (plan / normal)。kimi 式模式可见性。"""
        label = mode.strip() if mode else "zall"
        try:
            self._textarea.border_title = f"\u276f {label}"
        except Exception:
            pass

    def set_status_line(self, text: str = "") -> None:
        """输入框边框右下角状态行 (cwd · branch · ctx%, kimi 式底栏)。"""
        try:
            self._textarea.border_subtitle = text or ""
        except Exception:
            pass

    # ── 选择菜单 (确认门/选型/会话) ──
    def open_select(self, title: str, choices: list[tuple[str, str, str]], *, hint: str = "") -> None:
        """打开选择菜单并接管键盘 (方向/数字/Enter/Esc)。

        type-ahead 防护: 记录打开时刻, 宽限期内滞留的决策键被吞掉
        (防止模型运行时提前敲的 Enter 误批写盘操作)。"""
        self._select.open(title, choices, hint=hint)
        self._textarea.select_mode = True
        self._textarea.select_opened_at = time.monotonic()

    def close_select(self) -> None:
        self._select.close()
        self._textarea.select_mode = False

    @property
    def select_open(self) -> bool:
        return self._select.is_open

    def on_chat_text_area_select_prev(self, event: ChatTextArea.SelectPrev) -> None:
        self._select.move(-1)

    def on_chat_text_area_select_next(self, event: ChatTextArea.SelectNext) -> None:
        self._select.move(1)

    def on_chat_text_area_select_num(self, event: ChatTextArea.SelectNum) -> None:
        self._select.select_number(event.n)

    def on_chat_text_area_select_confirm(self, event: ChatTextArea.SelectConfirm) -> None:
        val = self._select.selected_value
        self.close_select()
        if val is not None:
            self.post_message(self.SelectChosen(val))

    def on_chat_text_area_select_cancel(self, event: ChatTextArea.SelectCancel) -> None:
        self.close_select()
        self.post_message(self.SelectCancelled())

    def on_chat_text_area_interrupt(self, event: ChatTextArea.Interrupt) -> None:
        """Esc (无菜单时) → 向 App 重投中断请求。"""
        self.post_message(self.Interrupt())

    def _refresh_menu(self) -> None:
        """v1.8/v2.x: 根据输入刷新菜单 — 输 / 弹命令面板 (模糊+core优先), 输 @ 弹文件补全。"""
        text = self._textarea.text
        # 命令面板: 单行且以 / 开头
        if text.startswith("/") and "\n" not in text and " " not in text:
            from zall.cli.commands import fuzzy_rank, get_palette_commands
            query = text[1:]
            all_cmds = get_palette_commands()
            if query:
                ranked = fuzzy_rank(query, all_cmds, limit=40)
                hint = "\u2191\u2193 scroll \u00b7 Tab complete \u00b7 Enter run \u00b7 Esc close"
            else:
                # 空 query: 全部命令 (core 优先), 滚动可见全部 — 不再只给 8 条
                ranked = sorted(all_cmds, key=lambda c: (not c[3], c[0]))
                hint = f"\u2191\u2193 scroll \u00b7 type to filter \u00b7 {len(all_cmds)} commands"
            # 面板内部名不带 / (与文件补全一致的无前缀契约; render/补全时统一加 prefix)
            matches = [(name.lstrip("/"), desc) for name, desc, _cat, _core in ranked]
            self._menu_kind = "command"
            self._menu.update_items(matches, prefix="/", hint=hint)
            self._textarea.menu_mode = bool(matches)
            return
        # 文件补全: 末尾正在输入的 @token (行首或空格后的 @, 其后无空白)
        fq = self._file_query(text)
        if fq is not None:
            matches = [(p, "") for p in self._workspace_file_matches(fq)]
            self._menu_kind = "file"
            hint = "\u2191\u2193 select \u00b7 Tab complete \u00b7 Esc close" if matches else ""
            self._menu.update_items(matches, prefix="@", hint=hint)
            self._textarea.menu_mode = bool(matches)
            return
        self._menu.hide()
        self._textarea.menu_mode = False

    @staticmethod
    def _file_query(text: str) -> str | None:
        """提取末尾 @token 查询 (委托共享 file_complete, REPL/TUI 一致)。"""
        from zall.cli.file_complete import file_query
        return file_query(text)

    def _all_workspace_files(self) -> list[str]:
        """惰性扫描工作区文件 (委托共享 file_complete)。"""
        from zall.cli.file_complete import list_workspace_files
        return list_workspace_files()

    def _workspace_file_matches(self, query: str, limit: int = 8) -> list[str]:
        """匹配 query 的文件 (委托共享 file_complete: basename 前缀优先 + 路径短优先)。"""
        from zall.cli.file_complete import workspace_file_matches
        return workspace_file_matches(query, limit=limit)

    def _complete_menu_selection(self) -> None:
        """用选中项补全输入 (命令 → /cmd ; 文件 → 替换末尾 @token)。"""
        sel = self._menu.selected_command
        if not sel:
            return
        if self._menu_kind == "file":
            import re
            text = self._textarea.text
            new = re.sub(r"((?:^|\s))@[^\s@]*$", lambda m: f"{m.group(1)}@{sel} ", text)
            self._textarea.text = new
        else:
            self._textarea.text = f"/{sel} "
        self._textarea.cursor_location = self._textarea.document.end
        self._menu.hide()
        self._textarea.menu_mode = False

    def on_text_area_changed(self, event: Any) -> None:
        """v1.8: 输入变化时刷新命令菜单。"""
        self._refresh_menu()

    def on_chat_text_area_menu_prev(self, event: ChatTextArea.MenuPrev) -> None:
        self._menu.move_selection(-1)

    def on_chat_text_area_menu_next(self, event: ChatTextArea.MenuNext) -> None:
        self._menu.move_selection(1)

    def on_chat_text_area_menu_dismiss(self, event: ChatTextArea.MenuDismiss) -> None:
        self._menu.hide()
        self._textarea.menu_mode = False

    def on_chat_text_area_menu_complete(self, event: ChatTextArea.MenuComplete) -> None:
        """Tab 补全选中项 (命令名 / 文件路径, 不执行)。"""
        self._complete_menu_selection()

    def on_chat_text_area_toggle_plan(self, event: ChatTextArea.TogglePlan) -> None:
        """Shift+Tab — 向 App 重投 plan 切换请求。"""
        self.post_message(self.TogglePlan())

    def on_chat_text_area_open_editor(self, event: ChatTextArea.OpenEditor) -> None:
        """Ctrl+O — 向 App 重投外部编辑器请求。"""
        self.post_message(self.OpenEditor())

    def on_chat_text_area_steer(self, event: ChatTextArea.Steer) -> None:
        """Ctrl+S — 取当前输入文本, 清空并向 App 重投 steer 请求。

        空输入也投递 (App 侧可据此从 pending 队列弹出最早一条来 steer)。
        """
        text = self._textarea.expanded_text.strip()
        self._textarea.clear()
        self._menu.hide()
        self._textarea.menu_mode = False
        self.post_message(self.Steer(text))

    def on_chat_text_area_submitted(self, event: ChatTextArea.Submitted) -> None:
        """Handle Enter submission from ChatTextArea (v1.7: 正确的提交路径)."""
        text = event.text.strip()
        # 菜单打开时: 文件补全 → 补全并继续编辑; 命令 → 补全为 /cmd 再执行
        if self._menu.visible_menu and self._menu.selected_command:
            if self._menu_kind == "file":
                self._complete_menu_selection()
                return
            text = f"/{self._menu.selected_command}"
            self._menu.hide()
            self._textarea.menu_mode = False
        if text:
            self._add_to_history(text)
            self.post_message(self.Submitted(text))
            self._textarea.clear()
            self._history_index = -1
            self._current_draft = ""

    def on_chat_text_area_history_prev(self, event: ChatTextArea.HistoryPrev) -> None:
        """Navigate history back (up arrow at first line)."""
        if not self._history:
            return
        if self._history_index == -1:
            self._current_draft = self._textarea.text
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        self._textarea.text = self._history[self._history_index]
        self._textarea.cursor_location = self._textarea.document.end

    def on_chat_text_area_history_next(self, event: ChatTextArea.HistoryNext) -> None:
        """Navigate history forward (down arrow at last line)."""
        if self._history_index < 0:
            return
        self._history_index += 1
        if self._history_index >= len(self._history):
            self._history_index = -1
            self._textarea.text = self._current_draft
            self._current_draft = ""
        else:
            self._textarea.text = self._history[self._history_index]
        self._textarea.cursor_location = self._textarea.document.end

    def _add_to_history(self, text: str) -> None:
        """Add text to input history, avoiding duplicates."""
        if not self._history or self._history[-1] != text:
            self._history.append(text)
        # Keep history bounded
        if len(self._history) > 100:
            self._history = self._history[-100:]

    def focus(self) -> None:
        self._textarea.focus()

    @property
    def value(self) -> str:
        return self._textarea.text

    @value.setter
    def value(self, v: str) -> None:
        self._textarea.text = v


# ──────────────────────────────────────────────────────────────────────────
# StatusBar widget — enhanced with dir, git branch, context window
# ──────────────────────────────────────────────────────────────────────────

class StatusBar(Widget):
    """Status bar showing model, directory, git branch, steps, tokens, mode.

    Inspired by Claude Code's StatusLine:
      - Model name (leftmost)
      - Current directory (truncated)
      - Git branch (if available)
      - Step count
      - Token usage
      - Permission mode (color-coded)
      - Context window usage

    Uses reactive attributes for live updates.
    """

    __test__ = False

    model: reactive[str] = reactive("")
    step: reactive[int] = reactive(0)
    tokens: reactive[str] = reactive("")
    mode: reactive[str] = reactive("")
    git_branch: reactive[str] = reactive("")
    cwd: reactive[str] = reactive("")
    context_pct: reactive[str] = reactive("")
    # v1.6: 流式状态 (活的状态指示 — 用户可感知 agent 正在工作)
    streaming: reactive[bool] = reactive(False)
    # v2.x: 排队消息数 (Enter during streaming → queue)
    queued: reactive[int] = reactive(0)
    # v2.x: 活的 spinner 帧 + 已耗时 (慢端点 ~10s/调用时, 让用户看到"在动", 不显得卡死)
    _spin: reactive[int] = reactive(0)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._stream_start: float | None = None
        self._tick_timer: Any = None

    def on_mount(self) -> None:
        # 每 0.2s 推进一帧 + 刷新已耗时 (仅 streaming 时真正变化)
        self._tick_timer = self.set_interval(0.2, self._tick, pause=False)

    def watch_streaming(self, streaming: bool) -> None:
        # streaming 起止 → 记录/清除起始时刻 (用于 elapsed)
        self._stream_start = time.time() if streaming else None

    def _tick(self) -> None:
        # 仅在生成中推进 spinner (触发 reactive 刷新); 空闲时不刷屏
        if self.streaming:
            self._spin = (self._spin + 1) % len(_G.SPINNER_FRAMES)

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $background;
        color: $text-muted;
    }
    """

    def render(self) -> Text:
        """Render the status bar as a single line of text."""
        parts: list[str] = []

        # Model name (Claude Code: leftmost, accent color)
        if self.model:
            parts.append(f" [{_C.ACCENT}]{self.model}[/]")
        else:
            parts.append(f" [{_C.ACCENT}]zall[/]")

        # v1.6: 流式状态活指示 (agent 正在生成时显示, 给用户可感知反馈)
        # v2.x: 动画 spinner + 已耗时秒数 — 慢端点 (~10s/调用) 下让用户确信"在动"。
        if self.streaming:
            frame = _G.SPINNER_FRAMES[self._spin % len(_G.SPINNER_FRAMES)]
            if self._stream_start is not None:
                secs = int(time.time() - self._stream_start)
                parts.append(f" [{_C.SELECT}]{frame} thinking {secs}s\u2026[/]")
            else:
                parts.append(f" [{_C.SELECT}]{frame} thinking\u2026[/]")
            parts.append(f" [{_C.SUBTLE}]esc to interrupt[/]")

        # Current directory (Claude Code: shows cwd)
        if self.cwd:
            # Truncate to fit
            cwd_display = self.cwd[:40] + ("..." if len(self.cwd) > 40 else "")
            parts.append(f" [{_C.INFO}]{cwd_display}[/]")

        # Git branch (Claude Code: shows branch)
        if self.git_branch:
            parts.append(f" [{_C.DIM}]{_G.BULLET} {self.git_branch}[/]")

        # Step count
        if self.step > 0:
            parts.append(f" [{_C.DIM}]{_G.BULLET} step {self.step}[/]")

        # Token usage
        if self.tokens:
            parts.append(f" [{_C.DIM}]{_G.BULLET} {self.tokens}[/]")

        # Context window usage (Claude Code: shows context %)
        if self.context_pct:
            parts.append(f" [{_C.DIM}]{_G.BULLET} ctx {self.context_pct}[/]")

        # v2.x: 排队消息数 (Enter during streaming → 队列)
        if self.queued:
            parts.append(f" [{_C.QUEUE}]{_G.BULLET} {self.queued} queued[/]")

        # Permission mode (Claude Code: color-coded mode indicator)
        if self.mode:
            mode_style = {
                "plan": _C.THINKING,
                "strict": _C.FAIL,
                "accept": _C.SUCCESS,
                "normal": _C.ACCENT,
                "auto": _C.SUCCESS,
            }.get(self.mode.lower(), _C.DIM)
            parts.append(f" [{mode_style}]{_G.BULLET} {self.mode}[/]")

        if not parts:
            return Text.from_markup(f"[{_C.ACCENT}] zall[/]")

        return Text.from_markup("".join(parts))


# ──────────────────────────────────────────────────────────────────────────
# ToolPanel widget — enhanced with diff rendering and color-coded types
# ──────────────────────────────────────────────────────────────────────────

class ToolPanel(Widget):
    """Collapsible tool call panel with diff support.

    Inspired by Claude Code's AssistantToolUseMessage + FileEditToolDiff:
      - Shows a summary of tool calls with expand/collapse toggle
      - Color-coded tool types (read=blue, write=gold, bash=orange)
      - Diff rendering for edit_file results (+/- lines in green/red)
      - Preview of tool output with truncation
      - Click to expand/collapse full output
    """

    __test__ = False

    def __init__(self, tool_id: str, args: dict[str, Any], success: bool, output: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._tool_id = tool_id
        self._args = args
        self._success = success
        self._output = output
        self._expanded = False

    def _get_tool_color(self) -> str:
        """Get the color for this tool type (Claude Code: type-aware colors)."""
        if self._tool_id in ("read_file", "grep", "glob", "list_dir", "search"):
            return _C.TOOL_READ  # steel_blue1
        elif self._tool_id in ("write_file", "edit_file", "batch_edit"):
            return _C.TOOL_WRITE  # gold1
        elif self._tool_id == "bash":
            return _C.TOOL_BASH  # dark_orange
        elif self._tool_id in ("codegraph", "code_understanding"):
            return _C.TOOL_CODE  # spring_green3
        return _C.ACCENT

    def render(self) -> Text | Panel:
        name = _display_tool_name(self._tool_id)
        icon = _G.OK if self._success else _G.FAIL
        tool_color = self._get_tool_color()
        preview = _key_arg(self._args)
        
        # Build summary line with type-aware color
        summary = f" {icon} [{tool_color}]{name}[/]"
        if preview:
            summary += f" [{_C.DIM}]{preview}[/]"
        
        if self._expanded and self._output:
            lines = self._output.strip().split("\n")
            MAX_SHOW = 10
            display = lines[:MAX_SHOW]
            
            # Check if this is a diff output (edit_file)
            if self._tool_id == "edit_file" and any(
                l.startswith("+") or l.startswith("-") for l in lines[:5]
            ):
                styled_lines: list[Text] = []
                for line in display:
                    line = line.rstrip("\r")
                    if not line:
                        continue
                    if line.startswith("+") and not line.startswith("+++"):
                        styled_lines.append(Text(f"  {line}", style="green"))
                    elif line.startswith("-") and not line.startswith("---"):
                        styled_lines.append(Text(f"  {line}", style="red"))
                    elif line.startswith("@@"):
                        styled_lines.append(Text(f"  {line}", style="cyan"))
                    else:
                        styled_lines.append(Text(f"  {line}", style="dim"))
                if len(lines) > MAX_SHOW:
                    styled_lines.append(Text(f"  ... ({len(lines) - MAX_SHOW} more)", style="grey50"))
                return Panel(
                    Text("\n").join(styled_lines) if styled_lines else Text(f"  {self._output[:200]}", style="grey74"),
                    border_style=tool_color,
                    padding=(0, 1),
                    expand=False,
                    title=summary,
                    title_align="left",
                )
            
            body = "\n".join(f"  {l}" for l in display)
            if len(lines) > MAX_SHOW:
                body += f"\n  ... ({len(lines) - MAX_SHOW} more)"
            return Text.from_markup(f"{summary}\n{body}")
        
        return Text.from_markup(summary)

    def on_click(self) -> None:
        self._expanded = not self._expanded
        self.refresh()


# ──────────────────────────────────────────────────────────────────────────
# ThinkingPanel widget
# ──────────────────────────────────────────────────────────────────────────

class ThinkingPanel(Widget):
    """Collapsible thinking/reasoning panel.

    Shows a compact preview of the model's reasoning, expandable on click.
    """

    __test__ = False

    def __init__(self, reasoning: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._reasoning = reasoning
        self._expanded = False

    def render(self) -> Text:
        lines = self._reasoning.strip().split("\n")
        if self._expanded:
            display = "\n".join(f"  [{_C.DIM}]{_markup_escape(l)}[/]" for l in lines)
            return Text.from_markup(f" [{_C.THINKING}]{_G.DIAMOND_OPEN} think[/]\n{display}")
        else:
            preview = shorten(" | ".join(l.strip() for l in lines if l.strip()), width=78)
            preview = _markup_escape(preview)
            return Text.from_markup(f" [{_C.THINKING}]{_G.DIAMOND_OPEN} think[/] [{_C.DIM}]{preview}[/]")

    def on_click(self) -> None:
        self._expanded = not self._expanded
        self.refresh()