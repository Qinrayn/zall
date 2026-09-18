"""Rich terminal renderer — consumes LoopEvent and renders to terminal.

Design (rich colors + Panel, TTY auto-degrades):
  - TTY: rich Console colors, tool results in Panels, model output as Markdown
  - Non-TTY (pipe/CI): auto-degrades to plain text (no colors/panels)
  - --json: one NDJSON line per event (not affected by TTY)

IPR constraints:
  IPR-0: invariant tests at tests/test_cli_render.py
  IPR-1: corresponds to DESIGN.md presentation layer projection
  IPR-3: only stdlib + rich, no model SDK
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, TextIO

from rich.ansi import AnsiDecoder
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.text import Text

from zall._util.string import shorten as _shorten  # G11: cell-width 截断
from zall._util.string import truncate as _truncate
from zall.core.accountability import base_judge
from zall.core.goal import GoalTriple
from zall.core.loop_events import LoopEvent

# ──────────────────────────────────────────────────────────────────────────
# Semantic color slots — 值由 cli/theme.py 在模块尾部 apply() 注入 (G6 单一色源)。
# 默认值 = obsidian 主题 (兼容直接 import _C 的测试/代码; theme 切换时被覆盖)。
# ──────────────────────────────────────────────────────────────────────────

class _C:
    """语义色槽位 (默认 Obsidian: warm amber primary, muted slate secondaries)。"""

    ACCENT = "gold1"            # Primary: tool names, icons, emphasis
    ACCENT2 = "dark_goldenrod"  # Secondary accent: borders, subtle highlights
    SUCCESS = "spring_green3"   # Tool success, judge met
    FAIL = "indian_red"         # Tool failure, judge not_met
    WARN = "dark_orange"        # Greylist, undecidable
    DANGER = "red3 bold"        # Blacklist, override
    INFO = "dodger_blue1"       # Secondary info: paths, summaries (融合盘: kimi 蓝)
    DIM = "grey50"              # Muted: timestamps, footnotes
    SUBTLE = "grey37"           # Extra muted: dividers, secondary labels
    MODEL = ""                  # Model output: no color (Markdown controls format)
    THINKING = "turquoise4"     # Reasoning: cool, contemplative
    # v0.6.0: 新增语义色
    STATUS_BAR = "grey50"       # 状态栏底色
    STATUS_BAR_TEXT = "grey82"  # 状态栏文字
    TOOL_READ = "steel_blue1"   # 读工具
    TOOL_WRITE = "gold1"        # 写工具
    TOOL_BASH = "dark_orange"   # bash 工具
    TOOL_CODE = "spring_green3" # 代码工具 (grep/glob/search)
    # v2.x: 交互状态语义色 (学 opencode 语义角色 primary/accent/info…, 复用既有基色)
    QUEUE = "steel_blue1"       # 排队消息计数 (Enter during streaming)
    STEER = "turquoise4"        # steer 注入 (Ctrl+S, 与 THINKING 同调: 介入当前思考)
    SELECT = "dodger_blue1"     # 融合盘: 流式 spinner + 活跃指示 (kimi 蓝)


# ── Glyph set — single unified icon vocabulary ──

class _G:
    """Unicode glyph vocabulary for the Obsidian theme."""
    TOOL = "\u25b8"          # ▸ right-pointing triangle: tool invocation
    BAR = "\u258c"           # ▌ left half block: exec/tool cell 左侧命令条 (Codex 口径)
    OK = "\u2713"            # ✓ check: success
    FAIL = "\u2717"          # ✗ cross: failure
    MET = "\u25cf"           # ● filled circle: goal met
    UNDECIDABLE = "\u25cb"   # ○ empty circle: undecidable
    WARN = "\u26a0"          # ⚠ warning
    SPINNER = "\u25e6"       # ◦ hollow dot: idle spinner
    DEPTH = "\u2502"         # │ vertical bar: nesting depth
    DEPTH_END = "\u2514"     # └ corner: end of nest
    ARROW = "\u2192"         # → right arrow: transition
    BULLET = "\u00b7"        # · middle dot: separator
    LINE = "\u2500"          # ─ horizontal line
    CORNER_TL = "\u256d"     # ╭ top-left
    CORNER_TR = "\u256e"     # ╮ top-right
    CORNER_BL = "\u2570"     # ╰ bottom-left
    CORNER_BR = "\u256f"     # ╯ bottom-right
    # Progress indicators
    TODO_DONE = "\u25c9"     # ◉ filled circle with dot
    TODO_ACTIVE = "\u25cc"   # ◌ dotted circle
    TODO_PENDING = "\u25e6"  # ◦ hollow dot
    # v1.1 交互优化 (借鉴 Claude Code / Grok Build)
    THINKING = "\u25ec"      # ◬ triangle: thinking indicator
    ELLIPSIS = "\u2026"      # … truncated
    DIAMOND = "\u25c6"       # ◆ filled diamond: completed turn
    DIAMOND_OPEN = "\u25c7"  # ◇ open diamond: in-progress turn
    # 多帧 spinner 动画 (借鉴 Claude Code: 几何符号, 无 emoji)
    # Grok Build 用盲文 ⠋⠙⠹⠸; Claude Code 用 ·✢✳✶✻✽
    # zall 选几何符号 (与 Obsidian 主题一致), 8 帧
    # 通用 spinner: 句点点阵动画 — 句点/空格在所有字体/编码都存在, 杜绝 tofu。
    # 原圆八分符 ◔◑◕ 在 Consolas 等常见字体缺字形 → 显示成方块, 故弃用。
    SPINNER_FRAMES = (
        ".  ",
        ".. ",
        "...",
        " ..",
        "  .",
        "   ",
    )


# ── ASCII 回退字形 (跨平台: 终端/编码不支持 unicode 时切换, 杜绝 tofu) ──
# 注: 无法探测字体缺字形 (那由默认选用通用字形规避); 此回退处理编码受限终端。
_G_ASCII: dict[str, Any] = {
    "TOOL": ">", "BAR": "|", "OK": "+", "FAIL": "x", "MET": "*", "UNDECIDABLE": "o",
    "WARN": "!", "SPINNER": ".", "DEPTH": "|", "DEPTH_END": "`",
    "ARROW": "->", "BULLET": "-", "LINE": "-",
    "CORNER_TL": "+", "CORNER_TR": "+", "CORNER_BL": "+", "CORNER_BR": "+",
    "TODO_DONE": "[x]", "TODO_ACTIVE": "[~]", "TODO_PENDING": "[ ]",
    "THINKING": "*", "ELLIPSIS": "...", "DIAMOND": "*", "DIAMOND_OPEN": "*",
    "SPINNER_FRAMES": (".  ", ".. ", "...", " ..", "  .", "   "),
}
_G_UNICODE: dict[str, Any] = {}  # 首次切换前备份 unicode 原值


def _unicode_supported() -> bool:
    """终端/编码是否支持 unicode 字形 (跨平台 tofu 防护)。

    依 stdout 编码判定: UTF-* → 支持; ascii/cp437/latin 等 → 回退 ASCII 字形。
    """
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in enc or enc == "cp65001"


def use_ascii_glyphs() -> None:
    """把 _G 字形切到 ASCII 回退 (受限终端启动时调一次)。幂等。"""
    if not _G_UNICODE:  # 备份原 unicode 值 (供还原/测试)
        for k in _G_ASCII:
            _G_UNICODE[k] = getattr(_G, k)
    for k, v in _G_ASCII.items():
        setattr(_G, k, v)


def use_unicode_glyphs() -> None:
    """还原 _G 字形为 unicode (供测试隔离)。"""
    for k, v in _G_UNICODE.items():
        setattr(_G, k, v)


def is_ascii_glyphs() -> bool:
    """当前是否处于 ASCII 回退字形模式 (装饰件/banner 据此降级, 免 mid-layout 混搭)。"""
    return _G.CORNER_TL == "+"


# ── Code syntax highlighting theme (pygments style) ──
# 值由 theme.apply() 管理 (G6); 默认 obsidian: one-dark + #1e1e1e。
# 消费方须用 `render.CODE_THEME` 模块属性访问 (非 from-import 拷贝), 否则切主题不生效。
CODE_THEME = "one-dark"
CODE_BG = "#1e1e1e"          # 代码块背景 (与 Screen 一致, 去断裂感)


# ── ANSI helpers for raw-stream writing (spinner/progress) ──
# Rich markup ([gold1]...[/]) only works via Console.print().
# Spinner writes \r to raw stream → must use ANSI escapes directly.
#
# G6: 表由 theme.build_ansi_map() 用 rich Color 自动派生 (模块尾部填充)。
# 此前手工表有 4 处色号错误 (spring_green3/dark_orange/steel_blue1/grey37
# 与 rich 同名色不一致), 自动派生顺带修正。

_ANSI_MAP: dict[str, str] = {}
_ANSI_RESET = "\033[0m"


def _ansi(color: str, text: str) -> str:
    """Wrap text in ANSI color for raw-stream output."""
    code = _ANSI_MAP.get(color, "")
    if not code:
        return text
    return f"{code}{text}{_ANSI_RESET}"


# 模式色 (借鉴 Claude Code: 不同权限模式用不同颜色)
class _ModeColor:
    """权限模式指示色 (prompt 前缀色)。"""
    NORMAL = "gold1"          # 默认模式
    PLAN = "dark_cyan"        # plan 模式 (只读姿态)
    ACCEPT = "spring_green3"  # auto-accept 模式
    STRICT = "indian_red"     # strict 模式 (全确认门)


# Shared Console (performance): avoid creating a new Console per render call.
_CONSOLE_CACHE: dict[int, Console] = {}
_CONSOLE_CACHE_MAX = 8


def _shared_console(out: Any) -> Console:
    key = id(out)
    c = _CONSOLE_CACHE.get(key)
    if c is not None and getattr(c, "file", None) is out:
        return c
    c = Console(file=out, color_system="auto", force_terminal=None,
                legacy_windows=None)
    # LRU eviction: remove oldest entry when full
    if len(_CONSOLE_CACHE) >= _CONSOLE_CACHE_MAX:
        # dict preserves insertion order in Python 3.7+ — pop first inserted key
        _CONSOLE_CACHE.pop(next(iter(_CONSOLE_CACHE)))
    _CONSOLE_CACHE[key] = c
    return c


def clear_console_cache() -> None:
    _CONSOLE_CACHE.clear()


# Tool name mapping: tool_id -> short display name
_TOOL_DISPLAY = {
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "batch_edit": "Batch",
    "bash": "Bash",
    "grep": "Grep",
    "glob": "Glob",
    "list_dir": "List",
    "todo_list": "Todo",
    "web_fetch": "Fetch",
    "spawn_subagent": "Agent",
    "read_image": "Image",
    "search": "Search",
}


def _display_tool_name(tool_id: str) -> str:
    return _TOOL_DISPLAY.get(tool_id, tool_id)


# think-tag fix: 部分 reasoning 模型 (DeepSeek-R1/v4 某些模式) 在 reasoning_content
# 中夹带 <think>...</think> 包装标记。这些是协议噪声, 对用户无意义, 显示前剥离。
# 也处理 content 偶发夹带的情况 (个别 provider 把边界 token 泄漏到 content delta)。
_RE_THINK_OPEN = re.compile(r"<think>\s*", re.IGNORECASE)
_RE_THINK_CLOSE = re.compile(r"\s*</think>\s*", re.IGNORECASE)


def _strip_think_tags(text: str) -> str:
    """Remove <think>/<\u200b/think> wrapper tags from reasoning/text content."""
    if not text:
        return text
    low = text.lower()
    if "<think>" not in low and "</think>" not in low:
        return text
    cleaned = _RE_THINK_OPEN.sub("", text)
    cleaned = _RE_THINK_CLOSE.sub("", cleaned)
    return cleaned


def fmt_elapsed_compact(elapsed: float) -> str:
    """工作态耗时紧凑格式 (Codex fmt_elapsed_compact 口径)。

    <60s 保留 0.1s 精度 ("12.4s"); 更长转人读格式: "1m 05s", "59m 59s",
    "1h 00m 00s" — 长任务下 "125.3s" 这类秒数串一眼读不出量级。
    """
    if elapsed < 59.95:  # 四舍五入后仍 <60s 才走亚分钟分支, 防 "60.0s"
        return f"{elapsed:.1f}s"
    secs = int(round(elapsed))  # 59.96 → 60 → "1m 00s" (int 截断会得 "0m 59s")
    if secs < 3600:
        return f"{secs // 60}m {secs % 60:02d}s"
    return f"{secs // 3600}h {(secs % 3600) // 60:02d}m {secs % 60:02d}s"


# ──────────────────────────────────────────────────────────────────────────
# Goal card rendering
# ──────────────────────────────────────────────────────────────────────────


def render_goal_card(goal: GoalTriple, judge_mode: str, out: Any) -> None:
    stmt = goal.statement
    goal_type = stmt.goal_type.value
    main_judge, aux_judge = base_judge(stmt.goal_type)

    if judge_mode == "system" or main_judge == "system":
        termination = "system"
    elif main_judge == "user":
        termination = "user"
    else:
        termination = "self"

    intent = _shorten(stmt.intent, width=80, placeholder="...")

    c = _shared_console(out)
    if hasattr(out, "isatty") and out.isatty():
        c.print(f"  [{_C.ACCENT}]{_G.CORNER_TL}{_G.LINE}{_G.LINE}[/] "
                f"[bold {_C.ACCENT}]Goal[/] "
                f"[{_C.DIM}]{_G.BULLET} {goal_type}[/] "
                f"[{_C.SUBTLE}]{_G.BULLET} {termination} judge"
                f" [{_C.DIM}]{stmt.rewrite_confidence:.0%}[/]")
        c.print(f"  [{_C.SUBTLE}]{_G.DEPTH}[/] [{_C.DIM}]{intent}[/]")
    else:
        c.print(f"  Goal {_G.BULLET} {goal_type} ({termination}, {stmt.rewrite_confidence:.0%})")
        c.print(f"    {intent}")


def _key_arg(args: dict[str, Any]) -> str:
    """Extract the most relevant argument for a single-line preview.
    
    Context-aware: shows the most useful info for each tool type.
    read_file: path + line range; bash: command; grep/glob: pattern + path.
    """
    # read_file: show path + line range
    if "path" in args and "offset" in args:
        p = str(args["path"])
        offset = args.get("offset", 1)
        limit = args.get("limit", 100)
        end = offset + limit - 1
        short = p[:50] + ("..." if len(p) > 50 else "")
        return f"{short}  L{offset}-{end}"
    for key in ("path", "command", "pattern", "query"):
        v = args.get(key)
        if v:
            s = str(v)
            return s[:60] + ("..." if len(s) > 60 else "")
    items = list(args.items())[:2]
    parts = []
    for k, v in items:
        s = str(v)[:40]
        parts.append(f"{k}={s}")
    return " ".join(parts)


# ──────────────────────────────────────────────────────────────────────────
# 控制台视觉词汇 (Argus 吸纳轮): 闪讯 / 面板头 / KV 表 / 引导面板 / 批跑进度
# — REPL 与 TUI 共享同一套 helper, 一次构建两处消费。
# 纪律: TTY 走 rich 结构 (Panel/Table), 非 TTY 降级纯文本 (管道/CI 输出契约
# 不变); 颜色一律 _C 语义槽位运行时取值 (G6 单一色源, 主题切换自动跟随)。
# ──────────────────────────────────────────────────────────────────────────


def _is_tty(out: Any) -> bool:
    return bool(hasattr(out, "isatty") and out.isatty())


def flash(out: Any, level: str, msg: str) -> None:
    """闪讯纪律 (Argus _flash 对标): [+]=成功 [!]=警告 [-]=错误 [i]=信息。

    全 CLI 统一的命令反馈前缀 — 用户扫一眼前缀就知道结果性质, 不用读全文。
    """
    styles = {
        "ok": ("[+]", _C.SUCCESS),
        "warn": ("[!]", _C.WARN),
        "err": ("[-]", _C.FAIL),
        "info": ("[i]", _C.INFO),
    }
    prefix, color = styles.get(level, styles["info"])
    line = Text()
    line.append(f"{prefix} ", style=f"bold {color}")
    line.append(msg, style=color)
    _shared_console(out).print(line)


def flash_ok(out: Any, msg: str) -> None:
    flash(out, "ok", msg)


def flash_warn(out: Any, msg: str) -> None:
    flash(out, "warn", msg)


def flash_err(out: Any, msg: str) -> None:
    flash(out, "err", msg)


def flash_info(out: Any, msg: str) -> None:
    flash(out, "info", msg)


def section_header(out: Any, title: str) -> None:
    """居中强调面板头 (Argus "Selected: X" 式) — 标记重要状态切换。"""
    c = _shared_console(out)
    if _is_tty(out):
        from rich.align import Align

        header = Text(f" {title} ", justify="center", style=f"bold {_C.ACCENT}")
        c.print()
        c.print(Align(Panel(header, expand=False, padding=(0, 2), style=_C.ACCENT2),
                      align="center"))
        c.print()
    else:
        c.print(f"== {title} ==")


_UNSET_VALUES = frozenset({"", "None", "Not set", "\u2014", "-"})


def kv_table(
    out: Any,
    title: str,
    pairs: list[tuple[str, Any]],
    *,
    caption: str = "",
    highlight: tuple[str, ...] = (),
) -> None:
    """Field/Value 信息表 (Argus module_info_table 对标)。

    highlight 中的字段值用成功色 (已设置/已变更); 空值统一暗色占位 —
    一眼看出"哪些已配、哪些还没有"。caption 放引导语 (⇒ Type 'run' 式)。
    """
    if _is_tty(out):
        from rich import box
        from rich.table import Table

        table = Table(
            title=title or None,
            title_style=f"bold {_C.ACCENT}",
            box=box.SIMPLE_HEAVY,
            caption=caption or None,
            caption_justify="center",
            caption_style=_C.DIM,
            expand=False,
            pad_edge=True,
        )
        table.add_column("Field", style=_C.INFO, no_wrap=True)
        table.add_column("Value", ratio=1, overflow="fold")
        for k, v in pairs:
            sv = str(v)
            if k in highlight and sv not in _UNSET_VALUES:
                style = _C.SUCCESS
            elif sv in _UNSET_VALUES:
                style = _C.SUBTLE
                sv = sv or "\u2014"
            else:
                style = ""
            table.add_row(str(k), Text(sv, style=style))
        from rich.align import Align

        c = _shared_console(out)
        c.print()
        c.print(Align(table, align="center"))
        c.print()
    else:
        if title:
            out.write(f"== {title} ==\n")
        w = max((len(str(k)) for k, _ in pairs), default=8)
        for k, v in pairs:
            sv = str(v)
            out.write(f"  {str(k).ljust(w)}  {sv if sv not in _UNSET_VALUES else '-'}\n")
        if caption:
            out.write(f"  {caption}\n")


def next_steps_panel(out: Any, lines: list[str], *, title: str = "Recommended Next Steps") -> None:
    """引导面板 (Argus recommendations_panel 对标): 动作后告诉用户下一步能做什么。

    空列表不渲染 (无话可说时保持安静, 不刷存在感)。
    """
    if not lines:
        return
    c = _shared_console(out)
    if _is_tty(out):
        body = Text()
        for i, ln in enumerate(lines):
            if i:
                body.append("\n")
            body.append(f"{_G.TOOL} ", style=f"bold {_C.ACCENT}")
            body.append(ln)
        c.print()
        c.print(Panel(
            body,
            title=Text(f" {title} ", style=f"bold {_C.ACCENT}"),
            border_style=_C.ACCENT2,
            expand=False,
            padding=(0, 2),
        ))
        c.print()
    else:
        c.print("  next steps:")
        for ln in lines:
            c.print(f"    - {ln}")


def batch_progress(console: Console | None = None) -> Any:
    """批跑进度预设 (Argus run_modules 对标): spinner+名称+n/m+已用时+ETA。

    transient=True — 跑完即清屏, 不留进度条残骸污染滚动历史。
    用法: with batch_progress() as prog: task = prog.add_task("run", total=n, name=...)
    """
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )

    return Progress(
        SpinnerColumn(),
        TextColumn(f"[bold {_C.INFO}]" + "{task.fields[name]}" + "[/]"),
        BarColumn(bar_width=None),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    )


# ──────────────────────────────────────────────────────────────────────────
# _StreamBuffer — 50ms timer-based batch flushing for streaming tokens
# ──────────────────────────────────────────────────────────────────────────


class _StreamBuffer:
    """Accumulates streaming tokens and flushes at 50ms intervals.

    Replaces the old character-level throttle mechanism with a proper
    timer-based approach inspired by Kimi Code's STREAMING_UI_FLUSH_MS=50.

    Design:
      - Tokens are accumulated in a buffer
      - On each add(), check if 50ms has elapsed since last flush
      - If yes, flush the accumulated text as a single write
      - If no, just accumulate (avoids flickering on fast token streams)
      - On flush_end(), flush any remaining buffer (final flush)
      - On interrupt(), flush remaining + append [Interrupted] marker

    Thread-safe: uses a Lock for buffer access.
    """

    __test__ = False

    def __init__(
        self,
        write_fn: Callable[[str], None],
        flush_interval_ms: float = 50.0,
        fix_fences: bool = True,
    ) -> None:
        self._buf = ""
        self._write_fn = write_fn
        self._flush_interval = flush_interval_ms / 1000.0
        self._last_flush = 0.0
        self._lock = threading.Lock()
        self._finalized = False
        self._fix_fences = fix_fences

    def add(self, token: str) -> None:
        """Add a token to the buffer. Flushes if 50ms has elapsed."""
        if not token:
            return
        with self._lock:
            if self._finalized:
                return
            self._buf += token
            now = time.monotonic()
            if self._last_flush == 0.0:
                self._last_flush = now
            elif now - self._last_flush >= self._flush_interval:
                self._flush_locked()

    def flush(self) -> None:
        """Force flush the buffer (used for final output)."""
        with self._lock:
            self._flush_locked(final=True)

    def flush_partial(self) -> None:
        """Flush buffer but keep it active (for breakpoint flushes).

        用于断句点 (空格/换行) 立即 flush, 但 buffer 继续接收后续 token。
        保留 fence fix (partial=True)。
        """
        with self._lock:
            if self._buf:
                self._flush_locked(final=False)

    def flush_end(self) -> None:
        """Flush remaining buffer and mark as finalized (no more tokens)."""
        with self._lock:
            self._finalized = True
            self._flush_locked(final=True)

    def interrupt(self) -> None:
        """Flush remaining buffer and append [Interrupted] marker.

        Preserves partial output so the user sees what was generated
        before the interruption, rather than losing it entirely.
        Only writes the marker if there was actual content.
        """
        with self._lock:
            had_content = bool(self._buf)
            self._flush_locked(final=True)
            if had_content:
                self._write_fn("\n[Interrupted]")

    def _flush_locked(self, final: bool = False) -> None:
        if not self._buf:
            return
        text = self._buf
        self._buf = ""
        self._last_flush = time.monotonic()
        if not final and self._fix_fences:
            text = self._trim_partial_fences(text)
        self._write_fn(text)

    @staticmethod
    def _trim_partial_fences(text: str) -> str:
        """Detect unclosed ``` fences and temporarily close them.

        During streaming, a code block opener (```python) may not yet
        have its closing ```. This causes flickering in rich Markdown
        rendering. We temporarily add a closing ``` so the fence is
        balanced for rendering purposes. The final flush removes
        this temporary fence (since final=True skips this fix).

        Claude Code uses trimPartialClosingFences for the same purpose.
        """
        count = text.count("```")
        if count % 2 == 1:
            return text + "\n```"
        return text

    @property
    def buffer_length(self) -> int:
        with self._lock:
            return len(self._buf)

    def reset(self) -> None:
        """Reset buffer state for a new streaming step."""
        with self._lock:
            self._buf = ""
            self._last_flush = 0.0
            self._finalized = False


class CliRenderer:
    """Obsidian terminal renderer (consumes LoopEvent).

    Three modes:
      text (default, TTY): rich colors + Panel + Markdown
      text (non-TTY): auto-degraded plain text
      json (--json): one NDJSON line per event

    v0.6.0: 新增状态栏 + 活动状态行 + 改进的 Thinking 显示
    """

    __test__ = False

    def __init__(
        self,
        *,
        json_mode: bool = False,
        stream: TextIO | None = None,
        verbose: bool = False,
        disable_spinner: bool = False,
    ) -> None:
        self._json = json_mode
        self._verbose = verbose
        self._disable_spinner = disable_spinner
        self._raw_stream = stream or sys.stderr
        self._write_lock = threading.Lock()
        self._console = _shared_console(self._raw_stream)
        self._is_tty = self._raw_stream.isatty()
        self._supports_ansi = self._detect_ansi_capability()
        self._anomaly_active = False  # anomaly 去重: 仅翻转沿打印
        self._todos: list[dict[str, str]] | None = None
        self._streamed_step: int | None = None
        self._throttle_buf = ""
        self._throttle_threshold = 8
        self._throttle_last_flush = 0.0
        self._spinner_active = False
        self._spinner_thread: threading.Thread | None = None
        self._spinner_stop = threading.Event()
        self._spinner_trigger = threading.Event()  # O9: persistent spinner trigger
        self._spinner_shutdown = threading.Event()  # v0.4.9 (A3): permanent exit
        self._spinner_step: int = 0
        self._spinner_model: str = ""
        self._spinner_start: float = 0.0
        self._model_call_start_time: float = 0.0
        self._model_call_elapsed: float = 0.0
        self._thinking_buf: str = ""
        self._thinking_active: bool = False
        self._thinking_display_buf: str = ""
        self._thinking_start_time: float = 0.0
        self._thinking_full: str = ""
        self._call_depth: int = 0
        self._spinner_token_count: int = 0  # v1.2: token 计数器
        self._tool_start_time: float = 0.0  # v1.2: 工具开始时间
        self._subagent_summary: dict[str, Any] = {}
        self._folded_tool_outputs: dict[int, str] = {}
        self._folded_tool_outputs_max = 64  # v0.4.9 (A4): evict oldest to prevent unbounded growth
        self._tool_step_counter: int = 0
        self._term_width = shutil.get_terminal_size().columns
        # v0.6.0: 状态栏
        self._status_model: str = ""
        self._status_branch: str = ""
        self._status_goal: str = ""
        self._status_plan: bool = False
        self._status_tokens: str = ""
        # 吸收轮: 上下文剩余 + 缓存命中 (Codex footer 对标)
        self._status_context: str = ""
        self._status_cache: str = ""
        self._status_dirty: bool = True
        # v0.6.0: 活动状态
        self._activity_label: str = ""
        self._activity_start: float = 0.0
        self._activity_phase: str = ""
        # v1.1 流式渲染: 50ms 定时合并刷新
        self._stream_buffer: _StreamBuffer | None = None

    @staticmethod
    def _detect_ansi_capability() -> bool:
        if sys.platform != "win32":
            return True
        try:
            import os as _os
            term = _os.environ.get("TERM", "")
            if term in ("xterm", "xterm-256color", "xterm-kitty", "alacritty",
                         "wezterm", "screen", "tmux", "ansi"):
                return True
            if _os.environ.get("WT_SESSION") or _os.environ.get("TERM_PROGRAM"):
                return True
            if _os.environ.get("ConEmuANSI") or _os.environ.get("CMDER_ROOT"):
                return True
            if _os.environ.get("TERM_PROGRAM") == "Hyper":
                return True
            return False
        except (ImportError, AttributeError):
            return False

    def _clear_line(self) -> None:
        with self._write_lock:
            self._term_width = shutil.get_terminal_size().columns
            if self._supports_ansi:
                self._raw_stream.write("\r\033[K")
            else:
                self._raw_stream.write("\r" + " " * self._term_width + "\r")
            self._raw_stream.flush()

    def set_verbose(self, verbose: bool) -> None:
        self._verbose = verbose

    def expand_tool(self, tool_idx: int) -> bool:
        if tool_idx not in self._folded_tool_outputs:
            return False
        body = self._folded_tool_outputs.pop(tool_idx)
        if self._is_tty:
            self._console.print(Panel(
                body,
                border_style=_C.ACCENT2,
                padding=(1, 2),
                expand=False,
                title=f"[{_C.SUBTLE}]#{tool_idx} expanded[/]",
                title_align="left",
            ))
        else:
            self._raw_stream.write(f"  #{tool_idx} expanded:\n")
            for line in body.split("\n"):
                self._raw_stream.write(f"    {line}\n")
            self._raw_stream.flush()
        return True

    def expand_all_tools(self) -> int:
        count = 0
        for idx in sorted(self._folded_tool_outputs.keys()):
            if self.expand_tool(idx):
                count += 1
        return count

    @property
    def folded_count(self) -> int:
        return len(self._folded_tool_outputs)

    def __call__(self, event: LoopEvent) -> None:
        if self._json:
            self._render_json(event)
        else:
            self._render_text(event)

    def _render_json(self, event: LoopEvent) -> None:
        line = json.dumps({
            "kind": event.kind,
            "step": event.step,
            "payload": event.payload,
        }, ensure_ascii=False)
        self._raw_stream.write(line + "\n")
        self._raw_stream.flush()

    def _render_text(self, event: LoopEvent) -> None:
        kind = event.kind
        p = event.payload
        step = event.step

        if kind == "model_call_start":
            self._render_model_call_start(step, p)
        elif kind == "model_token":
            self._stop_spinner()
            self._clear_thinking_line()
            self._render_model_token(step, p)
        elif kind == "model_thinking":
            self._stop_spinner()
            self._render_model_thinking(step, p)
        elif kind == "model_tool_call":
            self._stop_spinner()
            self._clear_thinking_line()
            self._render_model_tool_call(step, p)
        elif kind == "model_call":
            self._stop_spinner()
            self._render_model_call(step, p)
        elif kind == "step_progress":
            self._render_step_progress(step, p)
        elif kind == "gate_decision":
            self._render_gate(step, p)
        elif kind == "tool_call_start":
            self._render_tool_start(step, p)
        elif kind == "tool_call_end":
            self._render_tool_end(step, p)
        elif kind == "tool_rejected":
            self._render_tool_rejected(step, p)
        elif kind == "override":
            self._render_override(step, p)
        elif kind == "judge_result":
            self._render_judge(step, p)
        elif kind == "context_compaction":
            count = p.get("compacted_count", 0)
            reason = p.get("reason", "?")
            self._raw_stream.write(
                f"  [{_C.SUBTLE}]compact: {count} msg ({reason})[/]\n"
            )
            self._raw_stream.flush()
        elif kind == "perception_state":
            # v0.6.0: 感知状态更新 — 静默更新内部状态, 不显示
            self._status_dirty = True
            if self._is_tty:
                confidence = p.get("confidence", 0.0)
                anomaly = p.get("anomaly", False)
                # 去重 (2026-07-26): 只在 False→True 翻转沿打印一次,
                # 避免持续异常态下每步刷屏同一条警告
                if anomaly and not self._anomaly_active:
                    self._console.print(f"  [{_C.WARN}]{_G.WARN} anomaly detected (confidence: {confidence:.0%})[/]")
                self._anomaly_active = anomaly
        elif kind == "retry":
            # 重试可见性 (G3): 退避不再静默 — spinner 标签替换, 零闪烁。
            self._render_retry(step, p)
        elif kind in ("runaway", "length_exceeded", "error"):
            self._stop_spinner()
            self._render_error(step, kind, p)
        else:
            self._console.print(f"  [{_C.SUBTLE}]{_G.BULLET} {kind} (step {step})[/]")

    # ── Spinner: rotating braille precision pattern ──
    # O9: 单线程复用 (而非每次 model_call_start 创建新 Thread)

    _SPIN_FRAMES = ("\u28cb", "\u28d9", "\u28f6", "\u28e7", "\u28cf", "\u28df",
                     "\u28bf", "\u28fb", "\u28fd", "\u28fe")

    def _spinner_loop(self) -> None:
        """持久 spinner 线程: 循环等待 _spinner_trigger, 触发后旋转直到 _spinner_stop。

        v0.4.9 (A3): 不再因 _stop_spinner 退出, 而是回到 wait() 等待下次触发。
        真正的线程退出由 _spinner_shutdown 控制 (shutdown_spinner 调用)。
        v0.6.0: 显示活动标签 + 计时 (Grok Build 风格的活动状态行)。
        v1.2: 上下文感知 spinner (借鉴 Claude Code): token 计数 + interrupt 提示。
        """
        while not self._spinner_shutdown.is_set():
            self._spinner_trigger.wait()
            if self._spinner_shutdown.is_set():
                return
            if self._spinner_stop.is_set():
                self._spinner_trigger.clear()
                continue
            self._spinner_trigger.clear()
            idx = 0
            while not self._spinner_stop.is_set():
                if self._spinner_shutdown.is_set():
                    return
                elapsed = time.time() - self._spinner_start
                frame = self._SPIN_FRAMES[idx % len(self._SPIN_FRAMES)]
                idx += 1
                # v1.4: stall 检测 — 颜色渐变 (正常→警告→危险)
                if elapsed > 30:
                    frame_color = _ANSI_MAP.get(_C.FAIL, "")
                    stall_hint = " (taking long)"
                elif elapsed > 10:
                    frame_color = _ANSI_MAP.get(_C.WARN, "")
                    stall_hint = ""
                else:
                    frame_color = _ANSI_MAP.get(_C.ACCENT, "")
                    stall_hint = ""
                # 活动标签优先于模型名
                label = self._activity_label or self._spinner_model or "thinking"
                dim = _ANSI_MAP.get(_C.DIM, "")
                subtle = _ANSI_MAP.get(_C.SUBTLE, "")
                rst = _ANSI_RESET
                # 工作态状态行 (Codex StatusIndicator 对标):
                #   ‹frame› ‹label›  (12s • ctrl-c to interrupt)  1.2k tok
                # 括号段固定位置, 用户随时知道"跑了多久/怎么打断"; 详情在标签里。
                parts = f"  {frame_color}{frame}{rst} {frame_color}{label}{stall_hint}{rst}"
                if elapsed > 0.8:
                    inner = fmt_elapsed_compact(elapsed)
                    if elapsed > 2.0:
                        inner += f" {_G.BULLET} ctrl-c to interrupt"
                    parts += f" {dim}({inner}){rst}"
                if self._spinner_token_count > 0:
                    parts += f" {subtle}{self._spinner_token_count} tok{rst}"
                with self._write_lock:
                    if self._spinner_stop.is_set() or self._spinner_shutdown.is_set():
                        break
                    self._raw_stream.write(f"\r{parts}")
                    self._raw_stream.flush()
                time.sleep(0.08)

    def _start_spinner(self) -> None:
        """启动/重启 spinner (复用持久线程)。"""
        if self._spinner_active:
            return
        self._spinner_stop.clear()
        self._spinner_active = True
        self._spinner_trigger.set()

    def _stop_spinner(self) -> None:
        if not self._spinner_active:
            return
        self._spinner_stop.set()
        # 触发持久线程退出旋转循环, 回到 _spinner_trigger.wait() 等待
        self._spinner_trigger.set()
        # 不 join 也不设 None —— 持久线程继续存活等待下次触发
        # v0.4.9 (A3): 修复之前 unconditionally 设 _spinner_thread = None
        # 破坏持久线程设计, 导致每次 model_call 重建线程。
        self._clear_line()
        self._spinner_active = False

    def shutdown_spinner(self) -> None:
        """REPL 退出时真正停止持久 spinner 线程。

        与 _stop_spinner 不同: _stop_spinner 只是暂停旋转,
        线程继续存在等待下次触发; shutdown_spinner 发送退出信号
        并清理线程引用。
        """
        self._stop_spinner()
        # 发送永久退出信号, 线程在 _spinner_shutdown 检查时 return
        self._spinner_shutdown.set()
        self._spinner_trigger.set()
        if self._spinner_thread is not None:
            if self._spinner_thread.is_alive():
                self._spinner_thread.join(timeout=1.0)
            self._spinner_thread = None

    # ── v0.6.0: 状态栏 ──

    def update_status(
        self,
        *,
        model: str = "",
        branch: str = "",
        goal: str = "",
        plan: bool = False,
        tokens: str = "",
        context: str = "",
        cache: str = "",
    ) -> None:
        """更新状态栏信息 (context/cache 为吸收轮新增: 上下文剩余 + 缓存命中)。"""
        changed = (
            model != self._status_model
            or branch != self._status_branch
            or goal != self._status_goal
            or plan != self._status_plan
            or tokens != self._status_tokens
            or context != self._status_context
            or cache != self._status_cache
        )
        if changed:
            self._status_model = model
            self._status_branch = branch
            self._status_goal = goal
            self._status_plan = plan
            self._status_tokens = tokens
            self._status_context = context
            self._status_cache = cache
            self._status_dirty = True

    def render_status_bar(self, force: bool = False) -> None:
        """渲染状态栏 (1 行, 顶部)。

        force=True 绕过 dirty 门 — 命令后回显纪律 (Argus _print_status_bar 对标):
        每条斜杠命令执行完都重印一次当前状态, 用户始终知道"现在处于什么状态"。
        """
        if not self._is_tty or (not force and not self._status_dirty):
            return
        self._status_dirty = False
        parts = []
        # 左侧: 模型名
        if self._status_model:
            parts.append(f"[{_C.ACCENT}]{self._status_model}[/]")
        # Git 分支
        if self._status_branch:
            parts.append(f"[{_C.INFO}]{self._status_branch}[/]")
        # 目标类型
        if self._status_goal:
            parts.append(f"[{_C.SUCCESS}]{self._status_goal}[/]")
        # 规划模式
        if self._status_plan:
            parts.append(f"[{_C.THINKING}]plan[/]")
        # 上下文剩余 (Codex "NN% context left" 对标)
        if self._status_context:
            parts.append(f"[{_C.DIM}]{self._status_context}[/]")
        # 缓存命中 (Codex "(+ N cached)" 对标)
        if self._status_cache:
            parts.append(f"[{_C.SUCCESS}]{self._status_cache}[/]")
        # token 用量
        if self._status_tokens:
            parts.append(f"[{_C.DIM}]{self._status_tokens}[/]")

        if not parts:
            return
        sep = f" [{_C.SUBTLE}]{_G.BULLET}[/] "
        line = "  " + sep.join(parts)
        with self._write_lock:
            self._clear_line()
            self._console.print(line)

    # ── v0.6.0: 活动状态行 ──

    def set_activity(self, label: str, phase: str = "") -> None:
        """设置当前活动标签 (如 "Thinking", "Running", "Verifying")。"""
        self._activity_label = label
        self._activity_phase = phase
        if not self._activity_start:
            self._activity_start = time.time()

    def clear_activity(self) -> None:
        """清除活动状态。"""
        self._activity_label = ""
        self._activity_phase = ""
        self._activity_start = 0.0

    def _clear_thinking_line(self) -> None:
        if not self._thinking_active:
            return
        self._clear_line()
        self._thinking_active = False

    # ── Model call rendering ──

    def _render_model_call_start(self, step: int, p: dict[str, Any]) -> None:
        self._thinking_buf = ""
        self._thinking_active = False
        self._thinking_start_time = 0.0
        self._model_call_start_time = time.time()
        self._spinner_token_count = 0  # v1.2: 重置 token 计数
        # v1.2: 上下文感知 spinner — 显示 "Thinking" (借鉴 Claude Code)
        self.set_activity("Thinking")
        if not self._is_tty:
            # v1.5: 非 TTY 不打印 "step N ..." 噪音 (管道/CI 只需结果)
            return
        # O9: 首次调用时创建持久 spinner 线程, 后续复用
        if self._spinner_thread is None or not self._spinner_thread.is_alive():
            self._spinner_thread = threading.Thread(
                target=self._spinner_loop, daemon=True
            )
            self._spinner_thread.start()
        self._spinner_step = step
        self._spinner_model = p.get("model", "")
        self._spinner_start = time.time()
        self._start_spinner()

    def _render_model_thinking(self, step: int, p: dict[str, Any]) -> None:
        token = p.get("token", "")
        if not token:
            return
        # think-tag fix: 剥离 reasoning 模型夹带的 <think>/<​/think> 协议噪声。
        token = _strip_think_tags(token)
        if not token:
            return
        self._thinking_buf += token
        if self._thinking_start_time == 0.0:
            self._thinking_start_time = time.time()
        if not self._is_tty:
            return
        self._thinking_active = True
        self._thinking_display_buf += token
        last_char = token[-1]
        need_flush = (
            last_char in " \t\n.,;:!?"
            or len(token) >= 8
            or len(self._thinking_display_buf) >= 20
        )
        if not need_flush:
            return
        MAX_LINE = 78
        elapsed = time.time() - self._thinking_start_time
        display = self._thinking_display_buf.replace("\n", " | ")
        if len(display) > MAX_LINE - 20:
            display = "..." + display[-(MAX_LINE - 21):]
        display_line = f"  [{_C.THINKING}]{_G.BULLET}[/] [{_C.DIM}]{display}[/]  [{_C.SUBTLE}]({fmt_elapsed_compact(elapsed)})[/]"
        self._clear_line()
        with self._write_lock:
            self._console.print(display_line, end="")
            self._raw_stream.flush()
        self._thinking_display_buf = ""

    def _render_model_tool_call(self, step: int, p: dict[str, Any]) -> None:
        """Stream式 tool call 增量 — 展示模型正在构建的工具调用。

        v0.4.10: 非 TTY 下也输出 step 前缀, 与 _render_model_token 格式一致。
        """
        tool_calls = p.get("tool_calls", [])
        if not tool_calls:
            return
        args_preview = []
        for tc in tool_calls:
            tid = tc.get("tool_id", "?")
            args = tc.get("args", {})
            preview = _key_arg(args)
            if preview:
                args_preview.append(f"{tid}({preview})")
            else:
                args_preview.append(tid)
        preview = ", ".join(args_preview[:3])
        if len(args_preview) > 3:
            preview += f" ... (+{len(args_preview) - 3})"

        if self._is_tty:
            self._console.print(f"  [{_C.ACCENT}]{_G.TOOL}[/] [{_C.DIM}]{rich_escape(preview)}[/]")
        else:
            # 非 TTY: 输出 step 前缀以保持格式一致性
            if self._streamed_step != step:
                self._streamed_step = step
            with self._write_lock:
                self._raw_stream.write(f"  tool calls: {preview}\n")
                self._raw_stream.flush()

    def render_tool_call_preview(self, tool_id: str, partial_args: dict[str, Any]) -> str:
        """Render a preview of a tool call with partial arguments.

        Extracts the most relevant field from partial_args (which may be
        incomplete due to streaming JSON) and returns a single-line preview.

        This is used by the streaming layer to show real-time tool call
        previews before the full arguments are available.

        Examples:
          render_tool_call_preview("read_file", {"path": "x.py"})
          -> "Read(x.py)"
          render_tool_call_preview("bash", {"command": "ls -la"})
          -> "Bash(ls -la)"
        """
        name = _display_tool_name(tool_id)
        preview = _key_arg(partial_args)
        if preview:
            return f"{name}({preview})"
        return name

    def _render_thinking_block(self, reasoning: str) -> None:
        """v1.3: 克制的 Thinking 显示 (学 Kimi Code: 少即是多)。

        - 只显示第一行摘要 + 耗时
        - 不用框/树形装饰, 只用淡色前缀
        - 详细内容由 /verbose 展开

        think-tag fix: 部分 reasoning 模型 (如 DeepSeek-R1/v4 某些模式) 会在
        reasoning_content 中夹带 <think>...</think> 包装标记。这些是协议噪声,
        对用户无意义, 显示前剥离。
        """
        reasoning = _strip_think_tags(reasoning)
        if not reasoning:
            return
        self._thinking_full = reasoning
        elapsed = ""
        if self._thinking_start_time > 0:
            elapsed = f" {fmt_elapsed_compact(time.time() - self._thinking_start_time)}"
        if self._is_tty:
            lines = reasoning.strip().split("\n")
            # v1.3: 只显示第一行摘要 (克制、不喇叨)
            first_line = lines[0].strip()
            if len(first_line) > 72:
                first_line = first_line[:69] + "\u2026"
            more = f"  [{_C.SUBTLE}]+{len(lines) - 1}[/]" if len(lines) > 1 else ""
            self._console.print(
                f"  [{_C.THINKING}]\u25ec[/] [{_C.DIM}]{first_line}[/]"
                f"[{_C.SUBTLE}]{elapsed}[/]{more}"
            )
        else:
            lines = reasoning.strip().split("\n")
            MAX_SHOW = 3
            truncated = len(lines) > MAX_SHOW
            display_lines = lines[:MAX_SHOW]
            text = "\n".join(display_lines)
            if truncated:
                text += f"\n... ({len(lines) - MAX_SHOW} more)"
            self._raw_stream.write(f"  think{elapsed}:\n")
            for line in text.split("\n"):
                self._raw_stream.write(f"    {line}\n")
            self._raw_stream.flush()

    def _render_model_token(self, step: int, p: dict[str, Any]) -> None:
        """Streaming token rendering with 50ms batch flushing.

        Uses _StreamBuffer to accumulate tokens and flush at 50ms intervals.
        This prevents flickering and reduces write overhead compared to
        the old character-level throttle mechanism.

        Non-TTY (pipe) mode: writes directly without buffering for
        real-time pipe compatibility.
        """
        token = p.get("token", "")
        if not token:
            return
        # v1.2: 累计 token 计数 (spinner 显示用)
        self._spinner_token_count += 1
        if self._streamed_step != step:
            self._streamed_step = step
            # Create a new stream buffer for this step
            self._stream_buffer = _StreamBuffer(
                write_fn=lambda text: self._write_stream_text(text),
                flush_interval_ms=50.0,
                fix_fences=self._is_tty,
            )
        
        if not self._is_tty:
            # Non-TTY: direct write for pipe compatibility
            with self._write_lock:
                self._raw_stream.write(token)
                self._raw_stream.flush()
            return
        
        # TTY: use stream buffer for 50ms batch flushing
        self._stream_buffer.add(token)
        # 首个 token 或断句点 (空格/换行/句末标点) 立即 flush,
        # 给用户即时反馈 (消除首字延迟 + 文字流畅感)
        if self._spinner_token_count == 1 or (
            token and token[-1] in (" ", "\n", "\t", ".", ",", "\u3002", "\uff0c", ":", ";")
        ):
            self._stream_buffer.flush_partial()

    def _write_stream_text(self, text: str) -> None:
        """Write text to the raw stream with lock (used by _StreamBuffer)."""
        with self._write_lock:
            self._raw_stream.write(text)
            self._raw_stream.flush()

    def _flush_stream_buffer(self) -> None:
        """Flush the current stream buffer (end of streaming step)."""
        if self._stream_buffer is not None:
            self._stream_buffer.flush_end()
            self._stream_buffer = None

    def interrupt_stream(self) -> None:
        """Handle streaming interruption: preserve partial output.

        Flushes remaining buffer and appends [Interrupted] marker.
        This is called from repl_ui.py on KeyboardInterrupt.
        Unlike the old behavior (discard partial output), this preserves
        whatever tokens were already displayed so the user sees progress.
        """
        if self._stream_buffer is not None:
            self._stream_buffer.interrupt()
            self._stream_buffer = None

    def _flush_throttle(self) -> None:
        if self._throttle_buf:
            with self._write_lock:
                self._raw_stream.write(self._throttle_buf)
            self._raw_stream.flush()
            self._throttle_buf = ""
            self._throttle_last_flush = time.monotonic()

    def _render_model_call(self, step: int, p: dict[str, Any]) -> None:
        reasoning = p.get("reasoning", "")
        self._stop_spinner()
        self._clear_thinking_line()
        self.clear_activity()  # v1.2: model call 完成, 清除活动标签
        self._render_thinking_block(reasoning)

        if self._streamed_step == step:
            self._flush_stream_buffer()
            self._raw_stream.write("\n")
            self._raw_stream.flush()
            self._streamed_step = None
            self._render_token_usage(p)
            return

        content = p.get("content", "")
        tool_calls = p.get("tool_calls", [])

        if content:
            # think-tag fix: 剥离偶发泄漏到 content 的 <think>/<​/think> 标记。
            content = _strip_think_tags(content)
            if self._is_tty:
                # G7: 代码块跟随主题 code_theme (zall-ansi → ANSISyntaxTheme 实例)
                from zall.cli.syntax_theme import resolve_code_theme
                self._console.print(Markdown(content, code_theme=resolve_code_theme(CODE_THEME)))
            else:
                # v1.5: 非 TTY 输出完整内容, 不加 "step N - " 前缀
                self._raw_stream.write(content + "\n")
                self._raw_stream.flush()
        elif tool_calls:
            tool_names = [tc.get("tool_id", "?") for tc in tool_calls]
            preview = ", ".join(tool_names[:3])
            if len(tool_names) > 3:
                preview += f" ... (+{len(tool_names) - 3})"
            self._console.print(f"  [{_C.ACCENT}]{_G.TOOL}[/] [{_C.DIM}]{preview}[/]")
        else:
            hint = "empty response — try rephrasing or /model to switch"
            if self._is_tty:
                self._console.print(f"  [{_C.SUBTLE}]({hint})[/]")
            else:
                self._console.print(f"  step {step} - (empty - {hint})")

        self._render_token_usage(p)

    def _render_token_usage(self, p: dict[str, Any]) -> None:
        elapsed = ""
        if self._model_call_start_time > 0:
            t = time.time() - self._model_call_start_time
            self._model_call_elapsed = t
            if t >= 1.0:
                elapsed = f"  {_G.BULLET} {fmt_elapsed_compact(t)}"

        usage = p.get("usage", {})
        if not usage or not isinstance(usage, dict):
            return
        prompt = usage.get("prompt", 0)
        completion = usage.get("completion", 0)
        total = usage.get("total", 0) or (prompt + completion)
        if total == 0:
            return
        if self._is_tty:
            self._console.print(
                f"  [{_C.SUBTLE}]{_G.BULLET} {total} tokens[/] "
                f"[{_C.SUBTLE}](in {prompt} / out {completion})[/]"
                f"[{_C.SUBTLE}]{elapsed}[/]"
            )
        else:
            self._raw_stream.write(
                f"  tokens: {total} (in: {prompt} out: {completion}){elapsed}\n"
            )
            self._raw_stream.flush()

    # ── Gate rendering ──

    def _render_gate(self, step: int, p: dict[str, Any]) -> None:
        level = p.get("level", "")
        tool_id = p.get("tool_id", "?")
        if level == "greylist":
            if not self._is_tty:
                self._console.print(
                    f"  [{_C.WARN}]{_G.WARN} greylist:[/] {rich_escape(tool_id)} "
                    f"[{_C.SUBTLE}](needs confirm)[/]"
                )
        elif level == "blacklist":
            self._console.print(
                f"  [{_C.DANGER}]{_G.FAIL} BLACKLIST:[/] {rich_escape(tool_id)} "
                f"[{_C.SUBTLE}](blocked)[/]"
            )

    # ── Tool rendering ──

    def _render_tool_start(self, step: int, p: dict[str, Any]) -> None:
        """v0.6.0: 工具调用开始 — 类型感知颜色。v1.2: 上下文感知活动标签。"""
        tool_id = p.get("tool_id", "?")
        args = p.get("args", {})
        name = _display_tool_name(tool_id)
        preview = _key_arg(args)
        self._tool_start_time = time.time()  # v1.2: 记录工具开始时间

        # v1.2: 上下文感知 spinner — 显示当前工具活动 (借鉴 Claude Code)
        if preview:
            self.set_activity(f"{name}({preview[:40]})")
        else:
            self.set_activity(name)

        if tool_id == "spawn_subagent":
            self._call_depth += 1
            self._subagent_summary = {"steps": 0, "tools": 0, "depth": self._call_depth}

        # v0.6.0: 类型感知颜色
        if tool_id in ("read_file", "grep", "glob", "list_dir", "search"):
            color = _C.TOOL_READ
        elif tool_id in ("write_file", "edit_file", "batch_edit"):
            color = _C.TOOL_WRITE
        elif tool_id == "bash":
            color = _C.TOOL_BASH
        elif tool_id in ("codegraph", "code_understanding", "lsp_diagnostics"):
            color = _C.TOOL_CODE
        else:
            color = _C.ACCENT

        # Build depth indicator
        depth_prefix = ""
        if self._call_depth > 0:
            depth_prefix = "  " * self._call_depth

        if preview:
            # Codex exec cell 口径 (TTY): ▌ 命令条 + 工具名 + 参数预览 (dim);
            # 非 TTY 保持原形态 (管道/CI 输出契约不变)。
            if self._is_tty:
                self._console.print(
                    f"{depth_prefix}[{color}]{_G.BAR}[/] "
                    f"[{color}]{name}[/] "
                    f"[{_C.DIM}]{rich_escape(str(preview))}[/]"
                )
            else:
                self._console.print(
                    f"{depth_prefix}[{color}]{_G.TOOL} {name}[/] "
                    f"[{_C.DIM}]{rich_escape(str(preview))}[/]"
                )
        elif self._is_tty:
            self._console.print(
                f"{depth_prefix}[{color}]{_G.BAR}[/] [{color}]{name}[/]"
            )
        else:
            self._console.print(
                f"{depth_prefix}[{color}]{_G.TOOL} {name}[/]"
            )

    def render_tool_progress(self, tool_id: str, elapsed: float) -> None:
        """v1.3: 工具执行进度指示 — 长运行工具显示 elapsed timer。

        由外部 (loop observer) 在工具执行中定期调用 (每 2s)。
        修复: 使用 ANSI 转义而非 rich markup (raw stream 不解析 markup)。
        """
        if not self._is_tty:
            return
        if elapsed < 3.0:
            return  # 短工具不显示进度
        name = _display_tool_name(tool_id)
        frame = self._SPIN_FRAMES[int(elapsed * 2) % len(self._SPIN_FRAMES)]
        accent = _ANSI_MAP.get(_C.ACCENT, "")
        dim = _ANSI_MAP.get(_C.DIM, "")
        rst = _ANSI_RESET
        with self._write_lock:
            self._raw_stream.write(
                f"\r  {accent}{frame} {name}{rst} {dim}{fmt_elapsed_compact(elapsed)}{rst}"
            )
            self._raw_stream.flush()

    def _render_tool_end(self, step: int, p: dict[str, Any]) -> None:
        tool_id = p.get("tool_id", "?")
        success = p.get("success", False)
        output = p.get("output", "")
        error = p.get("error")
        body = output or error or "(no output)"
        artifacts = p.get("artifacts", {})
        self.clear_activity()  # v1.2: 工具完成, 清除活动标签

        # todo_list progress projection
        if (
            tool_id == "todo_list"
            and isinstance(artifacts, dict)
            and artifacts.get("todos")
        ):
            self._todos = artifacts["todos"]
            self._render_todo_list(self._todos)
            return

        # subagent completion
        if tool_id == "spawn_subagent":
            self._call_depth = max(0, self._call_depth - 1)
            sub_steps = artifacts.get("steps", 0) or 0
            sub_tools = artifacts.get("tool_calls", 0) or 0
            sub_result = "ok" if success else "error"
            self._console.print(
                f"  [{_C.SUBTLE}]{_G.DEPTH_END}[/] [{_C.DIM}]sub done: "
                f"{sub_steps}s {sub_tools}t ({sub_result})[/]"
            )
            return

        icon = _G.OK if success else _G.FAIL
        color = _C.SUCCESS if success else _C.FAIL

        depth_prefix = ""
        if self._call_depth > 0:
            depth_prefix = "  " * self._call_depth

        # verbose: full output in panel
        if self._verbose:
            if self._is_tty:
                self._console.print(f"{depth_prefix}[{color}]{icon}[/] "
                                   f"[{_C.ACCENT}]{_display_tool_name(tool_id)}[/]")
                # v0.6.0: bash 输出使用 ANSI 解码器
                if tool_id == "bash" and self._has_ansi_codes(body):
                    _render_ansi_body(self._console, body)
                else:
                    self._console.print(Panel(
                        body,
                        border_style=color,
                        padding=(1, 2),
                        expand=False,
                    ))
            else:
                first = body.split("\n")[0][:100]
                self._console.print(f"    {icon} {tool_id}: {first}")
            return

        # compact: name + summary + duration
        self._tool_step_counter += 1
        tool_idx = self._tool_step_counter
        summary = self._summarize_tool_output(tool_id, body)
        name = _display_tool_name(tool_id)
        # TTY: Codex exec cell 口径 — ▌ 开命令条, └ 收结果行 (工具名不重复);
        # 非 TTY (管道/CI): 保留 "icon + 工具名" 形态, 输出契约不变。
        if self._is_tty:
            head = f"{depth_prefix}[{_C.SUBTLE}]{_G.DEPTH_END}[/] [{color}]{icon}[/]"
            tail_name = ""
        else:
            head = f"{depth_prefix}{icon}"
            tail_name = f" {name}"
        duration = ""
        if isinstance(artifacts, dict):
            dur = artifacts.get("duration")
            if dur is not None:
                try:
                    duration = f" [{_C.SUBTLE}]{fmt_elapsed_compact(float(dur))}[/]"
                except (ValueError, TypeError):
                    pass
        # v1.2: 如果 artifacts 没有 duration, 用 _tool_start_time 计算 (借鉴 Claude Code)
        if not duration and self._tool_start_time > 0:
            elapsed = time.time() - self._tool_start_time
            if elapsed >= 0.5:
                duration = f" [{_C.SUBTLE}]{fmt_elapsed_compact(elapsed)}[/]"
            self._tool_start_time = 0.0

        body_lines = body.split("\n")
        # v0.2.5: 大幅提升折叠阈值 (5→200). Cursor 不折叠, zall 也不该默认折叠.
        # 只有超长输出(>200行)才折叠, 例如大型 build log.
        MAX_PREVIEW_LINES = 200
        needs_fold = len(body_lines) > MAX_PREVIEW_LINES and not self._verbose

        if needs_fold:
            # v0.4.9 (A4): LRU eviction — remove oldest entries when over limit
            if len(self._folded_tool_outputs) >= self._folded_tool_outputs_max:
                # dict preserves insertion order; pop the first (oldest) key
                _oldest = next(iter(self._folded_tool_outputs))
                self._folded_tool_outputs.pop(_oldest)
            self._folded_tool_outputs[tool_idx] = body
            preview_lines = body_lines[:MAX_PREVIEW_LINES]
            remaining = len(body_lines) - MAX_PREVIEW_LINES
            self._console.print(
                f"{head}{tail_name}"
                f" [{_C.DIM}]{rich_escape(str(summary))}[/]{duration}"
            )
            for line in preview_lines:
                truncated = _truncate(line, 100, placeholder="...")
                self._console.print(f"    [{_C.SUBTLE}]{_G.DEPTH}[/] {truncated}")
            self._console.print(
                f"    [{_C.SUBTLE}]{_G.DEPTH_END}[/] "
                f"[{_C.INFO}]{remaining} more lines (type \"/expand {tool_idx}\" to show all)[/]"
            )
        else:
            self._console.print(
                f"{head}{tail_name}"
                f" [{_C.DIM}]{rich_escape(str(summary))}[/]{duration}"
            )

        if tool_id == "edit_file" and isinstance(artifacts, dict) and artifacts.get("diff"):
            # v1.2: 传递文件路径给 diff 渲染 (Panel title 显示); G1: 真实起始行号
            _diff_path = artifacts.get("path") or artifacts.get("file_path", "")
            _start = int(artifacts.get("start_line") or 0)
            self._render_edit_diff(artifacts["diff"], file_path=str(_diff_path), start_line=_start)

    _RE_LINES = re.compile(r"Lines (\d+)-(\d+) of (\d+)")
    _RE_REPLACED = re.compile(r"Replaced (\d+) line")

    @staticmethod
    def _summarize_tool_output(tool_id: str, body: str) -> str:
        if tool_id == "bash":
            exit_line = ""
            first_out = ""
            for line in body.split("\n"):
                if line.startswith("exit_code:"):
                    exit_line = line.replace("exit_code:", "").strip()
                elif first_out == "" and line.strip() and not line.startswith("stdout:") \
                        and not line.startswith("stderr:") and not line.startswith("["):
                    first_out = line.strip()
            if exit_line:
                parts = [f"exit {exit_line}"]
                if first_out:
                    parts.append(first_out[:50])
                return " - ".join(parts)
            return body.split("\n")[0][:60]
        if tool_id == "read_file":
            for line in body.split("\n"):
                if line.startswith("Lines "):
                    m = CliRenderer._RE_LINES.search(line)
                    if m:
                        return f"{m.group(2)} lines"
            return body.split("\n")[0][:60]
        if tool_id == "list_dir":
            lines = [line for line in body.split("\n") if line.strip()]
            return f"{len(lines)} entries"
        if tool_id == "grep":
            if "(no matches)" in body:
                return "no matches"
            count = len([line for line in body.split("\n") if line.strip() and not line.startswith("...")])
            return f"{count} matches"
        if tool_id == "edit_file":
            for line in body.split("\n"):
                if "Replaced" in line:
                    m = CliRenderer._RE_REPLACED.search(line)
                    if m:
                        return f"replaced {m.group(1)} lines"
            return _shorten(body.split("\n")[0], width=60, placeholder="...")
        first_line = _shorten(body.split("\n")[0], width=60, placeholder="...")
        total = len(body)
        return f"{first_line} ({total} chars)"

    def _render_edit_diff(self, diff: str, file_path: str = "", start_line: int = 0) -> None:
        """G1 (kimi 对标): 结构化 diff 面板 — 行号列 + 整行背景色 + 词级内联高亮。

        start_line: 真实文件起始行号 (artifacts["start_line"]); 0 = 未知, 行号从 1 计。
        解析/渲染失败回退旧版文本着色路径 (不阻塞工具结果展示)。
        """
        if self._is_tty:
            try:
                from zall.cli import diff_render as _dr
                offset = start_line - 1 if start_line > 0 else 0
                hunks, truncated = _dr.parse_unified_hunks(diff, line_offset=offset)
                if hunks:
                    self._console.print(
                        _dr.render_diff_panel(file_path, hunks, truncated=truncated)
                    )
                    return
            except Exception:
                pass  # 回退旧版渲染
            self._render_edit_diff_legacy(diff, file_path)
        else:
            for line in diff.split("\n"):
                self._raw_stream.write(f"    {line}\n")
            self._raw_stream.flush()

    def _render_edit_diff_legacy(self, diff: str, file_path: str = "") -> None:
        """v1.2 文本着色回退路径 (diff_render 解析失败时)。"""
        styled: list[Text] = []
        all_lines = diff.split("\n")
        MAX_DIFF_LINES = 50
        truncated = len(all_lines) > MAX_DIFF_LINES
        display_lines = all_lines[:MAX_DIFF_LINES]
        current_line_no = 0

        for line in display_lines:
            # Strip trailing \r (Windows CRLF → terminal \r acts as carriage return)
            line = line.rstrip("\r")
            if not line:
                continue
            if line.startswith("@@"):
                # 解析 hunk header: @@ -old_start,old_count +new_start,new_count @@
                m = re.search(r"\+(\d+)", line)
                if m:
                    current_line_no = int(m.group(1))
                styled.append(Text(line, style="cyan"))
            elif line.startswith("+") and not line.startswith("+++"):
                # v1.2: 行号前缀
                prefix = f"{current_line_no:>4} " if current_line_no else "     "
                styled.append(Text(f"{prefix}{line}", style="green"))
                current_line_no += 1
            elif line.startswith("-") and not line.startswith("---"):
                styled.append(Text(f"     {line}", style="red"))
                # 删除行不增加 new line number
            elif line.startswith("+++") or line.startswith("---"):
                styled.append(Text(line, style="bold"))
            else:
                # v1.2: 上下文行用 grey37 (更易读, 借鉴 Claude Code)
                prefix = f"{current_line_no:>4} " if current_line_no else "     "
                styled.append(Text(f"{prefix}{line}", style="grey37"))
                current_line_no += 1

        if truncated:
            remaining = len(all_lines) - MAX_DIFF_LINES
            styled.append(Text(f"     ... {remaining} more lines (/expand to show all)", style="grey50"))

        # v1.2: Panel title 显示文件路径 (借鉴 Claude Code)
        title = f"[{_C.INFO}]{file_path}[/]" if file_path else None
        self._console.print(Panel(
            Text("\n").join(styled),
            border_style=_C.SUBTLE,
            padding=(0, 1),
            expand=False,
            title=title,
            title_align="left",
        ))

    @staticmethod
    def _has_ansi_codes(text: str) -> bool:
        """v0.6.0: 检测文本是否包含 ANSI 转义码。"""
        return bool(re.search(r'\x1b\[[0-9;]*[a-zA-Z]', text))

    def _render_tool_rejected(self, step: int, p: dict[str, Any]) -> None:
        tool_id = p.get("tool_id", "?")
        self._console.print(f"    [{_C.FAIL}]{_G.FAIL} {tool_id}:[/] rejected by user")

    # ── Todo list rendering ──

    def _render_todo_list(self, todos: list[dict[str, str]]) -> None:
        _ICON = {
            "completed": _G.TODO_DONE,
            "in_progress": _G.TODO_ACTIVE,
            "pending": _G.TODO_PENDING,
        }
        _COLOR = {
            "completed": _C.SUCCESS,
            "in_progress": _C.ACCENT,
            "pending": _C.SUBTLE,
        }

        def _line(t: dict[str, str]) -> tuple[str, str, str]:
            st = t.get("status", "pending")
            icon = _ICON.get(st, _G.TODO_PENDING)
            color = _COLOR.get(st, _C.SUBTLE)
            label = t.get("content", "")
            if st == "in_progress":
                af = t.get("active_form")
                if af:
                    label = f"{label}  [{_C.DIM}]({af})[/]"
            return icon, color, label

        if self._is_tty:
            styled: list[Text] = []
            for t in todos:
                icon, color, label = _line(t)
                styled.append(Text(f" {icon} {label}", style=color))
            done = sum(1 for t in todos if t.get("status") == "completed")
            # Build a clean header-style panel
            header = f"[{_C.ACCENT}]tasks[/] [{_C.SUBTLE}]{done}/{len(todos)} done[/]"
            self._console.print(Panel(
                Text("\n").join(styled),
                border_style=_C.ACCENT2,
                padding=(1, 2),
                expand=False,
                title=header,
                title_align="left",
            ))
        else:
            done = sum(1 for t in todos if t.get("status") == "completed")
            self._raw_stream.write(f"  tasks ({done}/{len(todos)} done):\n")
            for t in todos:
                icon, _color, label = _line(t)
                self._raw_stream.write(f"    {icon} {label}\n")
            self._raw_stream.flush()

    # ── Override & Judge ──

    def _render_override(self, step: int, p: dict[str, Any]) -> None:
        tool_id = p.get("tool_id", "?")
        self._console.print(
            f"    [{_C.DANGER}]{_G.FAIL} {tool_id}:[/] OVERRIDDEN (audit logged)"
        )

    def _render_judge(self, step: int, p: dict[str, Any]) -> None:
        state = p.get("state", "?")
        report = p.get("report", "")
        reason = p.get("reason", "")

        # v1.1: differentiate "no judge configured (Q&A mode)" vs "judge ran, undecidable"
        is_no_judge = reason in ("no judge", "no main judge")

        if is_no_judge:
            # Neutral indicator for Q&A mode (no judge configured)
            color = _C.DIM
            icon = _G.BULLET
            label = f"{icon} no judge (Q&A mode)"
        else:
            color = {
                "met": _C.SUCCESS,
                "not_met": _C.FAIL,
                "undecidable": _C.WARN,
            }.get(state, _C.DIM)
            icon = {"met": _G.MET, "not_met": _G.FAIL, "undecidable": _G.UNDECIDABLE}.get(state, _G.BULLET)
            label = f"{icon} {state}"

        line = f"  [{color}]{label}[/]"
        if report:
            line += f" [{_C.SUBTLE}]{_G.BULLET} {rich_escape(str(report[:80]))}[/]"
        self._console.print(line)

    def _render_retry(self, step: int, p: dict[str, Any]) -> None:
        """重试可见性 (G3): "retrying (n/N) in Xs: <原因>"。

        TTY: 只换 spinner 活动标签 (不插行, 零闪烁);
        非 TTY: 落一行纯文本 (日志/管道可审计)。
        文案单一真相源: adapters.base.RETRY_REASON。
        """
        from zall.adapters.base import RETRY_REASON
        category = p.get("category", "")
        reason = RETRY_REASON.get(category, category or "error")
        attempt = p.get("attempt", 0)
        max_attempts = p.get("max_attempts", 0)
        delay = p.get("delay", 0)
        line = f"retrying ({attempt}/{max_attempts}) in {delay}s: {reason}"
        if self._is_tty:
            # spinner 用 \r 重绘同一行 — 不能插行打印, 原因并入活动标签。
            self.set_activity(
                f"Retrying {attempt}/{max_attempts} · {reason} · {delay}s wait",
                "retry",
            )
        else:
            self._raw_stream.write(f"  {line}\n")
            self._raw_stream.flush()

    def _render_error(self, step: int, kind: str, p: dict[str, Any]) -> None:
        """v0.6.0: 改进的错误显示 — 更多上下文, 更清晰的视觉层次。"""
        err = p.get("error", "")
        err_type = p.get("type", "")
        msg = str(err)[:200]
        if err_type:
            prefix = f"[{_C.FAIL}]{_G.FAIL}[/] [{_C.FAIL}]{err_type}[/]"
        else:
            prefix = f"[{_C.FAIL}]{_G.FAIL} {kind}[/]"
        self._console.print(
            f"  {prefix} [{_C.SUBTLE}](step {step})[/]: "
            f"{rich_escape(msg)}"
        )
        # 如果错误包含建议, 显示
        if "hint:" in msg.lower():
            hint = msg[msg.lower().index("hint:"):]
            self._console.print(f"    [{_C.INFO}]{_G.BULLET} {hint}[/]")

    # ── Step progress rendering (v0.6.0 UX) ──

    def _render_step_progress(self, step: int, p: dict[str, Any]) -> None:
        """v0.6.0: 步骤进度指示 — 感知阶段 + 活动标签更新。"""
        msg = p.get("message", "")
        phase = p.get("phase", "")
        if not msg:
            return
        # v1.5: 更新活动标签 (驱动 spinner 上下文感知)
        if phase == "perception":
            self.set_activity("Perceiving", phase)
        elif phase == "context":
            self.set_activity("Preparing", phase)
        elif phase == "model":
            self.set_activity("Thinking", phase)
        # v1.5: 阶段进度不再逐行打印 (噪音) — spinner 已传达活动状态。
        # 仅 verbose 模式保留 (供调试/审计)。借鉴 Claude Code/Kimi: 安静即优雅。
        if not self._verbose:
            return
        if self._is_tty:
            self._console.print(
                f"  [{_C.SUBTLE}]{_G.BULLET} step {step}:[/] {msg}"
            )
        else:
            self._raw_stream.write(f"  step {step}: {msg}\n")
            self._raw_stream.flush()

    # ── v1.2: 上下文 Footer 提示 (借鉴 Claude Code) ──

    def render_contextual_hint(self, state: str = "idle") -> None:
        """v1.2: 根据当前状态动态显示相关提示 (借鉴 Claude Code footer hints)。

        state:
          - "idle": 空闲等待输入
          - "tool": 工具执行中
          - "permission": 权限等待
          - "streaming": 流式输出中
        """
        if not self._is_tty:
            return
        hints = {
            "idle": (
                f"  [{_C.SUBTLE}]/help commands"
                f" {_G.BULLET} Ctrl-D exit"
                f" {_G.BULLET} Ctrl-R search history"
                f" {_G.BULLET} /plan read-only[/]"
            ),
            "tool": (
                f"  [{_C.SUBTLE}]Ctrl-C interrupt"
                f" {_G.BULLET} /expand show folded[/]"
            ),
            "permission": (
                f"  [{_C.SUBTLE}]y allow"
                f" {_G.BULLET} n reject"
                f" {_G.BULLET} a always"
                f" {_G.BULLET} e edit[/]"
            ),
            "streaming": (
                f"  [{_C.SUBTLE}]Ctrl-C interrupt (preserves output)[/]"
            ),
        }
        hint = hints.get(state, hints["idle"])
        with self._write_lock:
            self._console.print(hint)


# ──────────────────────────────────────────────────────────────────────────
# ANSI-aware terminal output rendering (v0.6.0)
# ──────────────────────────────────────────────────────────────────────────


_ANSI_DECODER = AnsiDecoder()


def _render_ansi_body(console: Console, body: str) -> None:
    """v0.6.0: 使用 Rich 的 ANSI 解码器渲染带 ANSI 转义码的终端输出。

    Grok Build 使用 VTE 解析器, zall 使用 Rich 的 AnsiDecoder (轻量级替代)。
    支持: 颜色/SGR、光标移动(简单)、清除行(简单)。
    不支持: 复杂光标定位、 alternate screen、复杂转义序列。
    """
    try:
        rich_texts = _ANSI_DECODER.decode(body)
        for rt in rich_texts:
            console.print(rt)
    except Exception:
        # 降级: 纯文本输出
        console.print(body)


# ──────────────────────────────────────────────────────────────────────────
# Egress summary
# ──────────────────────────────────────────────────────────────────────────


def render_egress_summary(
    run_id: str,
    final_state: str,
    step_count: int,
    tool_calls: int,
    model_calls: int,
    error: str | None,
    session_dir: str | None,
    *,
    stream: TextIO | None = None,
    usage: dict[str, int] | None = None,
    modified_files: list[str] | None = None,
    judge_ran: bool = False,
) -> None:
    s = stream or sys.stderr
    console = _shared_console(s)

    # v1.1: differentiate "no judge (Q&A mode)" vs "judge ran, undecidable"
    is_no_judge = final_state == "undecidable" and not judge_ran and not error

    if is_no_judge:
        color = _C.DIM
        icon = _G.BULLET
        display_state = "no judge (Q&A mode)"
    else:
        color = {
            "met": _C.SUCCESS,
            "not_met": _C.FAIL,
            "undecidable": _C.WARN,
        }.get(final_state, _C.DIM)
        icon = {"met": _G.MET, "not_met": _G.FAIL, "undecidable": _G.UNDECIDABLE}.get(final_state, _G.BULLET)
        display_state = final_state

    # Summary line with clean separators
    parts = [
        f"[{color}]{icon} {display_state}[/]",
        f"[{_C.DIM}]{step_count} steps[/]",
        f"[{_C.DIM}]{tool_calls} tools[/]",
        f"[{_C.DIM}]{model_calls} models[/]",
    ]
    console.print(f"  [{_C.ACCENT2}]{_G.LINE * 3}[/]")
    console.print("  " + f" [{_C.SUBTLE}]{_G.BULLET}[/] ".join(parts))

    if usage:
        total = int(usage.get("prompt", 0) or 0) + int(usage.get("completion", 0) or 0)
        if total > 0:
            console.print(
                f"  [{_C.SUBTLE}]{_G.DEPTH}[/] tokens: [{_C.ACCENT}]{total:,}[/] "
                f"[{_C.SUBTLE}](in {int(usage.get('prompt', 0) or 0):,} "
                f"/ out {int(usage.get('completion', 0) or 0):,})[/]"
            )
    if modified_files:
        file_count = len(modified_files)
        console.print(
            f"  [{_C.SUBTLE}]{_G.DEPTH}[/] modified: [{_C.ACCENT}]{file_count}[/] "
            f"[{_C.SUBTLE}]file(s)[/]"
        )
        for f in modified_files[:5]:
            console.print(f"    [{_C.SUBTLE}]{_G.BULLET}[/] [{_C.DIM}]{f}[/]")
        if file_count > 5:
            console.print(f"    [{_C.SUBTLE}]{_G.BULLET} ... {file_count - 5} more[/]")
    if error:
        console.print(f"  [{_C.FAIL}]{_G.FAIL} {error}[/]")
    if session_dir:
        console.print(f"  [{_C.SUBTLE}]{_G.BULLET} session: {session_dir}[/]")


# ──────────────────────────────────────────────────────────────────────────
# G6: 应用生效主题 (env ZALL_THEME > config [ui].theme > attic 希腊美学)。
# 必须在模块尾部: theme.apply() 写 _C/_ModeColor/_ANSI_MAP/CODE_*。
# theme.py 顶层不 import render — 无循环。
# ──────────────────────────────────────────────────────────────────────────
from zall.cli import theme as _theme_mod  # noqa: E402

_theme_mod.apply(_theme_mod.active())