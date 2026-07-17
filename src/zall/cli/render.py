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
from typing import Any, TextIO

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.text import Text

from zall.core.accountability import base_judge
from zall.core.goal import GoalTriple
from zall.core.loop_events import LoopEvent


# ──────────────────────────────────────────────────────────────────────────
# Semantic color palette — "Obsidian" theme
#
# Warm amber primary with muted slate tones. Feels like precision
# instrumentation rather than a toy CLI. Avoids plasticky bright colors.
# ──────────────────────────────────────────────────────────────────────────

class _C:
    """Obsidian color scheme: warm amber primary, muted slate secondaries."""

    ACCENT = "gold1"            # Primary: tool names, icons, emphasis
    ACCENT2 = "dark_goldenrod"  # Secondary accent: borders, subtle highlights
    SUCCESS = "spring_green3"   # Tool success, judge met
    FAIL = "indian_red"         # Tool failure, judge not_met
    WARN = "dark_orange"        # Greylist, undecidable
    DANGER = "red3 bold"        # Blacklist, override
    INFO = "steel_blue1"        # Secondary info: paths, summaries
    DIM = "grey50"              # Muted: timestamps, footnotes
    SUBTLE = "grey37"           # Extra muted: dividers, secondary labels
    MODEL = ""                  # Model output: no color (Markdown controls format)
    THINKING = "turquoise4"     # Reasoning: cool, contemplative


# ── Glyph set — single unified icon vocabulary ──

class _G:
    """Unicode glyph vocabulary for the Obsidian theme."""
    TOOL = "\u25b8"          # ▸ right-pointing triangle: tool invocation
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


# Shared Console (performance): avoid creating a new Console per render call.
_CONSOLE_CACHE: dict[int, "Console"] = {}
_CONSOLE_CACHE_MAX = 8


def _shared_console(out: Any) -> "Console":
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

    intent = stmt.intent
    if len(intent) > 80:
        intent = intent[:77] + "..."

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


class CliRenderer:
    """Obsidian terminal renderer (consumes LoopEvent).

    Three modes:
      text (default, TTY): rich colors + Panel + Markdown
      text (non-TTY): auto-degraded plain text
      json (--json): one NDJSON line per event
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
        self._subagent_summary: dict[str, Any] = {}
        self._folded_tool_outputs: dict[int, str] = {}
        self._folded_tool_outputs_max = 64  # v0.4.9 (A4): evict oldest to prevent unbounded growth
        self._tool_step_counter: int = 0
        self._term_width = shutil.get_terminal_size().columns

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
        """
        while not self._spinner_shutdown.is_set():
            self._spinner_trigger.wait()
            if self._spinner_shutdown.is_set():
                return
            if self._spinner_stop.is_set():
                # 被唤醒但 stop 已设置 (边缘情况): 回到等待
                self._spinner_trigger.clear()
                continue
            self._spinner_trigger.clear()
            idx = 0
            while not self._spinner_stop.is_set():
                if self._spinner_shutdown.is_set():
                    return
                elapsed = time.time() - self._spinner_start
                frame = self._SPIN_FRAMES[idx % len(self._SPIN_FRAMES)]
                label = self._spinner_model or "model"
                idx += 1
                if elapsed > 0.8:
                    status = f"  {frame} {label} {elapsed:.1f}s"
                else:
                    status = f"  {frame} {label}"
                with self._write_lock:
                    if self._spinner_stop.is_set() or self._spinner_shutdown.is_set():
                        break
                    self._raw_stream.write(f"\r{status}")
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
        if not self._is_tty:
            self._raw_stream.write(f"  step {step} ...\n")
            self._raw_stream.flush()
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
        display_line = f"  [{_C.THINKING}]{_G.BULLET}[/] [{_C.DIM}]{display}[/]  [{_C.SUBTLE}]({elapsed:.1f}s)[/]"
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

    def _render_thinking_block(self, reasoning: str) -> None:
        if not reasoning:
            return
        self._thinking_full = reasoning
        elapsed = ""
        if self._thinking_start_time > 0:
            elapsed = f" {time.time() - self._thinking_start_time:.1f}s"
        if self._is_tty:
            lines = reasoning.strip().split("\n")
            MAX_LINES = 5
            truncated = len(lines) > MAX_LINES
            display_lines = lines[:MAX_LINES]

            self._console.print(f"  [{_C.THINKING}]{_G.CORNER_TL}{_G.LINE}{_G.LINE}[/] "
                              f"[{_C.THINKING}]think{elapsed}[/]")
            for line in display_lines:
                self._console.print(f"  [{_C.SUBTLE}]{_G.DEPTH}[/] [{_C.DIM}]{line}[/]")
            if truncated:
                self._console.print(
                    f"  [{_C.SUBTLE}]{_G.DEPTH_END}[/] [{_C.INFO}]{len(lines) - MAX_LINES} more lines "
                    f"({_G.ARROW} /verbose to expand)[/]"
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
        token = p.get("token", "")
        if not token:
            return
        if self._streamed_step != step:
            self._streamed_step = step
            self._throttle_buf = ""
            if not self._is_tty:
                self._console.print(f"  step {step} - ", end="")

        if not self._is_tty:
            if self._streamed_step != step:
                self._streamed_step = step
                with self._write_lock:
                    self._raw_stream.write(f"  step {step} - ")
            with self._write_lock:
                self._raw_stream.write(token)
                self._raw_stream.flush()
            return

        self._throttle_buf += token
        now = time.monotonic()
        last_char = token[-1]
        need_flush = (
            last_char in " \t\n.,;:!?"
            or ("\u4e00" <= last_char <= "\u9fff")
            or ("\u3000" <= last_char <= "\u303f")
            or len(self._throttle_buf) >= self._throttle_threshold
            or (bool(self._throttle_buf) and (now - self._throttle_last_flush) >= 0.016)
        )
        if need_flush:
            with self._write_lock:
                self._raw_stream.write(self._throttle_buf)
                self._raw_stream.flush()
                self._throttle_buf = ""
            self._throttle_last_flush = now

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
        self._render_thinking_block(reasoning)

        if self._streamed_step == step:
            self._flush_throttle()
            self._raw_stream.write("\n")
            self._raw_stream.flush()
            self._streamed_step = None
            self._render_token_usage(p)
            return

        content = p.get("content", "")
        tool_calls = p.get("tool_calls", [])

        if content:
            if self._is_tty:
                self._console.print(Markdown(content))
            else:
                first_line = content.split("\n")[0][:80]
                self._console.print(f"  step {step} - {first_line}")
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
                elapsed = f"  {_G.BULLET} {t:.1f}s"

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
        tool_id = p.get("tool_id", "?")
        args = p.get("args", {})
        name = _display_tool_name(tool_id)
        preview = _key_arg(args)

        if tool_id == "spawn_subagent":
            self._call_depth += 1
            self._subagent_summary = {"steps": 0, "tools": 0, "depth": self._call_depth}

        # Build depth indicator
        depth_prefix = ""
        if self._call_depth > 0:
            depth_prefix = "  " * self._call_depth

        if preview:
            self._console.print(
                f"{depth_prefix}[{_C.ACCENT}]{_G.TOOL} {name}[/] "
                f"[{_C.DIM}]{rich_escape(str(preview))}[/]"
            )
        else:
            self._console.print(
                f"{depth_prefix}[{_C.ACCENT}]{_G.TOOL} {name}[/]"
            )

    def _render_tool_end(self, step: int, p: dict[str, Any]) -> None:
        tool_id = p.get("tool_id", "?")
        success = p.get("success", False)
        output = p.get("output", "")
        error = p.get("error")
        body = output or error or "(no output)"
        artifacts = p.get("artifacts", {})

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
        duration = ""
        if isinstance(artifacts, dict):
            dur = artifacts.get("duration")
            if dur is not None:
                try:
                    duration = f" [{_C.SUBTLE}]{float(dur):.1f}s[/]"
                except (ValueError, TypeError):
                    pass

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
                f"{depth_prefix}[{color}]{icon}[/] [{_C.ACCENT}]{name}[/]"
                f" [{_C.DIM}]{rich_escape(str(summary))}[/]{duration}"
            )
            for line in preview_lines:
                truncated = line[:100] + "..." if len(line) > 100 else line
                self._console.print(f"    [{_C.SUBTLE}]{_G.DEPTH}[/] {truncated}")
            self._console.print(
                f"    [{_C.SUBTLE}]{_G.DEPTH_END}[/] "
                f"[{_C.INFO}]{remaining} more lines (type \"/expand {tool_idx}\" to show all)[/]"
            )
        else:
            self._console.print(
                f"{depth_prefix}[{color}]{icon}[/] [{_C.ACCENT}]{name}[/]"
                f" [{_C.DIM}]{rich_escape(str(summary))}[/]{duration}"
            )

        if tool_id == "edit_file" and isinstance(artifacts, dict) and artifacts.get("diff"):
            self._render_edit_diff(artifacts["diff"])

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
            return body.split("\n")[0][:60]
        first_line = body.split("\n")[0]
        if len(first_line) > 60:
            first_line = first_line[:60] + "..."
        total = len(body)
        return f"{first_line} ({total} chars)"

    def _render_edit_diff(self, diff: str) -> None:
        if self._is_tty:
            styled: list[Text] = []
            for line in diff.split("\n"):
                # Strip trailing \r (Windows CRLF → terminal \r acts as carriage return)
                line = line.rstrip("\r")
                if not line:
                    continue
                if line.startswith("+") and not line.startswith("+++"):
                    styled.append(Text(line, style="green"))
                elif line.startswith("-") and not line.startswith("---"):
                    styled.append(Text(line, style="red"))
                elif line.startswith("@@"):
                    styled.append(Text(line, style="cyan"))
                else:
                    styled.append(Text(line, style="dim"))
            self._console.print(Panel(
                Text("\n").join(styled),
                border_style=_C.SUBTLE,
                padding=(0, 1),
                expand=False,
            ))
        else:
            for line in diff.split("\n"):
                self._raw_stream.write(f"    {line}\n")
            self._raw_stream.flush()

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
        color = {
            "met": _C.SUCCESS,
            "not_met": _C.FAIL,
            "undecidable": _C.WARN,
        }.get(state, _C.DIM)
        icon = {"met": _G.MET, "not_met": _G.FAIL, "undecidable": _G.UNDECIDABLE}.get(state, _G.BULLET)
        line = f"  [{color}]{icon} {state}[/]"
        if report:
            line += f" [{_C.SUBTLE}]{_G.BULLET} {rich_escape(str(report[:80]))}[/]"
        self._console.print(line)

    def _render_error(self, step: int, kind: str, p: dict[str, Any]) -> None:
        err = p.get("error", "")
        self._console.print(
            f"  [{_C.FAIL}]{_G.FAIL} {kind}[/] [{_C.SUBTLE}](step {step})[/]: "
            f"{rich_escape(str(err)[:100])}"
        )


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
) -> None:
    s = stream or sys.stderr
    console = _shared_console(s)
    color = {
        "met": _C.SUCCESS,
        "not_met": _C.FAIL,
        "undecidable": _C.WARN,
    }.get(final_state, _C.DIM)
    icon = {"met": _G.MET, "not_met": _G.FAIL, "undecidable": _G.UNDECIDABLE}.get(final_state, _G.BULLET)

    # Summary line with clean separators
    parts = [
        f"[{color}]{icon} {final_state}[/]",
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