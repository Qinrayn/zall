"""zall.cli.commands.system — System & meta commands.

Extracted from _legacy.py (v0.2.1 refactor).
Commands: /help, /about, /version, /exit, /clear, /doctor, /init,
          /checkpoint, /revert, /fix, /review

IPR constraints:
  IPR-3: only stdlib + rich, no model SDK
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from rich.align import Align
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from zall.cli.commands._common import (
    _CATEGORY_CONTEXT,
    _CATEGORY_NAV,
    _CATEGORY_SESSION,
    _CATEGORY_TOOLS,
    _auto_step_loop,
    _cmd_init_simple,
    _print_about,
    _print_advanced_help,
    _print_help,
    slash_command,
)
from zall.cli.render import _C, _shared_console
from zall.core.verifiability import EventType, RunRecorder


# Extracted from _legacy.py lines 335-379
@slash_command("/help", aliases=("/h",), description="show this help", category=_CATEGORY_NAV)
def cmd_help(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if arg:
        _print_help(out, cmd_name=arg)
    else:
        _print_help(out)
    return "handled"


@slash_command("/advanced", description="show all advanced commands", category=_CATEGORY_NAV)
def cmd_advanced(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """v0.6.0: 显示所有高级命令 (隐藏于 /help 之外)。"""
    _print_advanced_help(out)
    return "handled"


@slash_command("/about", description="project philosophy", category=_CATEGORY_NAV)
def cmd_about(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    _print_about(out)
    return "handled"


@slash_command("/version", aliases=("/v",), description="show version", category=_CATEGORY_NAV)
def cmd_version(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    from zall import __version__
    out.write(f"  zall {__version__}\n")
    return "handled"


@slash_command("/exit", aliases=("/quit", "/q"), description="exit", category=_CATEGORY_NAV)
def cmd_exit(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    out.write("  bye\n")
    return "exit"


@slash_command("/clear", aliases=("/new",), description="clear screen and start a new chat",
               category=_CATEGORY_NAV)
def cmd_clear(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """清屏 + 起新对话 (Codex /clear; /new 同义 — 新对话不继承上一段上下文)。

    吸收轮: 重置时把缓存统计一并清零, 否则新对话的命中率会被上一段污染。
    """
    if state is None:
        state = {}
    if hasattr(out, "isatty") and out.isatty():
        out.write("\033[2J\033[H")
        out.flush()
    else:
        out.write("  " + "-" * 40 + "\n")
        out.flush()
    state.pop("resume_messages", None)
    state.pop("_loop", None)
    state["usage"] = {"prompt": 0, "completion": 0}
    state.pop("ctx_tokens", None)
    _stats = state.get("cache_stats")
    if _stats is not None and hasattr(_stats, "reset"):
        try:
            _stats.reset()
        except Exception:
            pass
    state.pop("_artifact_files", None)
    state.pop("_added_files", None)
    return "clear"



@slash_command("/banner", description="re-print the startup banner", category=_CATEGORY_NAV)
def cmd_banner(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """重印启动屏 (Argus do_banner 对标; Ctrl-L 清屏后常用)。"""
    from zall.cli.repl_ui import _print_banner
    st = state or {}
    branch = None
    try:
        from zall.cli.environment import get_cached_cwd_meta
        branch = get_cached_cwd_meta(st).git_branch or None
    except Exception:
        pass
    _print_banner(
        out,
        model=st.get("model"),
        branch=branch,
        max_steps=st.get("max_steps", 0),
        verbose=bool(st.get("verbose")),
        plan=bool(st.get("plan_mode")),
    )
    return "handled"


@slash_command("/history", description="show recent input history (Ctrl-R searches)", category=_CATEGORY_NAV)
def cmd_history(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """最近输入历史 (cmd2 history 对标; 交互式反向搜索仍是 Ctrl-R)。"""
    limit = int(arg.strip()) if arg.strip().isdigit() else 20
    from zall.cli.prompt import _HISTORY_FILE
    try:
        raw_lines = _HISTORY_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        raw_lines = []
    # prompt_toolkit FileHistory 格式: 首行原样, 续行以 "+" 开头
    entries: list[str] = []
    cur: list[str] = []
    for raw in raw_lines:
        if raw.startswith("+"):
            if cur:
                cur.append(raw[1:])
        else:
            if cur:
                entries.append("\n".join(cur))
            cur = [raw]
    if cur:
        entries.append("\n".join(cur))
    entries = [e for e in entries if e.strip()]
    if not entries:
        out.write("  (no history yet)\n")
        return "handled"
    recent = entries[-limit:][::-1]
    out.write(f"  recent history ({len(recent)}, newest first) \u00b7 Ctrl-R to search\n")
    for i, t in enumerate(recent, 1):
        lines = t.splitlines()
        suffix = f"  \u2026+{len(lines) - 1}" if len(lines) > 1 else ""
        out.write(f"    {i:>3}  {lines[0]}{suffix}\n")
    return "handled"



# Extracted from _legacy.py lines 771-899
# ──────────────────────────────────────────────────────────────────────
# Checkpoint & Revert
# ──────────────────────────────────────────────────────────────────────


@slash_command("/status", aliases=("/st",),
               description="show session config and token usage", category=_CATEGORY_NAV)
def cmd_status(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """会话状态一览 (Codex /status 对标): 配置 + 用量 + 缓存命中。

    与 /doctor 的分工: /doctor 诊断环境 (依赖/网络/目录), /status 只看"当前这个
    会话在用什么、花了多少、缓存命中如何" — Codex 的 status 是同一定位。
    """
    from zall.cli.render import kv_table
    st = state or {}
    model = str(st.get("model") or "unset")
    mode = "plan" if st.get("plan_mode") else ("strict" if st.get("strict") else "fast")
    branch = ""
    try:
        from zall.cli.environment import get_cached_cwd_meta
        branch = get_cached_cwd_meta(st).git_branch or ""
    except Exception:
        pass
    pairs: list[tuple[str, Any]] = [
        ("model", model),
        ("mode", mode),
        ("cwd", str(Path.cwd())),
    ]
    if branch:
        pairs.append(("git branch", branch))
    steps = getattr(loop, "step_count", None) if loop is not None else None
    if steps is not None:
        pairs.append(("steps", steps))
    ctx = int(st.get("ctx_tokens", 0) or 0)
    if ctx:
        from zall.cli.repl_ui import _format_status_context
        pairs.append(("context", _format_status_context(st)))
    usage = st.get("usage") or {}
    if usage and (int(usage.get("prompt", 0) or 0) or int(usage.get("completion", 0) or 0)):
        from zall.core.cache_stats import format_tokens
        prompt = int(usage.get("prompt", 0) or 0)
        cached = int(usage.get("cached", 0) or 0)
        write = int(usage.get("cache_write", 0) or 0)
        completion = int(usage.get("completion", 0) or 0)
        pairs.append(("tokens in", f"{format_tokens(prompt)}"
                                    + (f" ({format_tokens(cached)} cached)" if cached else "")))
        if write:
            pairs.append(("cache write", format_tokens(write)))
        pairs.append(("tokens out", format_tokens(completion)))
    stats = st.get("cache_stats")
    if stats is not None:
        try:
            pairs.append(("cache", stats.format_summary()))
            if getattr(stats, "prefix_changes", 0):
                pairs.append(("prefix", f"{stats.prefix_changes} invalidated (system/tools changed)"))
        except Exception:
            pass
    # 会话落盘位置 (Codex /status 显示 rollout 路径的同位物)
    try:
        from zall.cli.orchestrator import _sessions_dir
        pairs.append(("sessions", str(_sessions_dir())))
    except Exception:
        pass
    # 链哈希头 (zall 特有: §6.1 可复现 timeline 的当前锚点)
    try:
        recorder = getattr(loop, "recorder", None)
        tail = str(getattr(recorder, "tail_hash", "") or "")
        if tail:
            pairs.append(("chain head", tail[:16]))
    except Exception:
        pass
    _has_usage = any(k == "tokens in" for k, _v in pairs)
    kv_table(out, "session status", pairs,
             caption=("usage is session-cumulative; cache% = cached input / total input"
                      if _has_usage else ""))
    return "handled"


# Codex "? for shortcuts" 对标: 控制台快捷键卡片 (TUI 的 ? 浮层同源信息)
_SHORTCUT_ROWS: list[tuple[str, str]] = [
    ("Ctrl-D", "exit (empty line)"),
    ("Ctrl-C", "interrupt the running turn"),
    ("Ctrl-R", "search input history"),
    ("Ctrl-P / Ctrl-N", "previous / next history entry"),
    ("Ctrl-L", "clear screen"),
    ("Ctrl-W / Ctrl-U", "delete word / line"),
    ("Alt-Enter", "insert newline (multi-line input)"),
    ("\\ + Enter", "continue on the next line"),
    ("Enter Enter", "submit after multi-line paste"),
    ("Tab", "complete commands, arguments, @files"),
    ("@path", "reference a file in the message"),
    ("/", "command palette (/help lists all)"),
    ("?", "this card"),
    ("/science", "research workbench (modules/use/run/auto)"),
]


@slash_command("/keys", aliases=("/shortcuts",),
               description="keyboard shortcuts and input tips", category=_CATEGORY_NAV)
def cmd_keys(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """快捷键卡片 (Codex "? for shortcuts" 对标)。"""
    _print_shortcuts(out)
    return "handled"


def _print_shortcuts(out: Any) -> None:
    """渲染快捷键卡片 (TTY 走 rich 表, 非 TTY 走纯文本 — 管道契约不变)。"""
    if hasattr(out, "isatty") and out.isatty():
        console = _shared_console(out)
        console.print()
        title = Text(" ? shortcuts ", justify="center", style=f"bold {_C.ACCENT}")
        console.print(Align(Panel(title, expand=False, padding=(0, 2), style=_C.ACCENT2),
                            align="center"))
        console.print()
        key_col, desc_col = [], []
        for key, desc in _SHORTCUT_ROWS:
            key_col.append(key)
            desc_col.append(desc)
        # 两列并排 (短卡片, 不占满屏)
        half = (len(_SHORTCUT_ROWS) + 1) // 2
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("key", style=_C.ACCENT, no_wrap=True)
        table.add_column("desc", style=_C.DIM)
        table.add_column("key2", style=_C.ACCENT, no_wrap=True)
        table.add_column("desc2", style=_C.DIM)
        for i in range(half):
            left_k, left_d = _SHORTCUT_ROWS[i]
            right = _SHORTCUT_ROWS[i + half] if i + half < len(_SHORTCUT_ROWS) else ("", "")
            table.add_row(left_k, left_d, right[0], right[1])
        console.print(Align(table, align="center"))
        console.print()
    else:
        out.write("  shortcuts:\n")
        for key, desc in _SHORTCUT_ROWS:
            out.write(f"    {key:18s} {desc}\n")
    out.flush()


@slash_command("/mcp", description="list configured MCP servers and tools", category=_CATEGORY_TOOLS)
def cmd_mcp(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """MCP 概览 (Codex /mcp 对标): 配置的 server + 已注册工具 (verbose 列工具名)。

    zall 的 MCP 是延迟后台加载 (§9.2.11), 所以这里显示"配置了什么"与"已连上并
    注册了什么"两件事 — 两者不一致本身就是诊断信息。
    """
    st = state or {}
    verbose = arg.strip().lower() in ("verbose", "-v", "--verbose")
    from zall.cli.render import kv_table
    tools = list(st.get("_mcp_tools") or [])
    configured: list[Any] = []
    try:
        from zall.mcp.config import load_mcp_config
        configured = list(load_mcp_config(project_path=str(Path.cwd())))
    except Exception:
        configured = []
    pairs: list[tuple[str, Any]] = [
        ("configured servers", str(len(configured))),
        ("registered tools", str(len(tools))),
    ]
    if configured:
        names = ", ".join(getattr(s, "name", "?") for s in configured)
        pairs.append(("servers", names))
    if verbose and tools:
        by_server: dict[str, list[str]] = {}
        for t in tools:
            by_server.setdefault(str(getattr(t, "server_name", "?")), []).append(
                str(getattr(t, "tool_id", "?"))
            )
        for srv, tids in sorted(by_server.items()):
            pairs.append((srv, ", ".join(sorted(tids))))
    kv_table(out, "mcp", pairs,
             caption="(deferred loading: tools appear once connected)"
                     if not tools and configured else "")
    return "handled"


@slash_command("/checkpoint", description="manage file snapshots", category=_CATEGORY_TOOLS)
def cmd_checkpoint(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    cmgr = loop.checkpoint_manager if loop is not None and hasattr(loop, "checkpoint_manager") else None
    if cmgr is None:
        out.write("  no active session with checkpoint manager\n")
        return "handled"

    parts = arg.split() if arg else []
    subcmd = parts[0] if parts else "list"

    if subcmd in ("list", "ls"):
        cps = cmgr.list_checkpoints()
        if not cps:
            out.write("  no checkpoints\n")
            return "handled"
        out.write(f"  checkpoints ({len(cps)}):\n")
        for cp in cps:
            label = cp.label or "(unnamed)"
            files_info = f"{cp.file_count} file(s)" if cp.file_count > 0 else "no files"
            out.write(f"    {cp.checkpoint_id:22s}  {label:30s}  {files_info}\n")
        return "handled"
    elif subcmd == "save":
        label = " ".join(parts[1:]) if len(parts) > 1 else f"manual_{int(time.time())}"
        entry = cmgr.save_checkpoint(label=label)
        if entry:
            out.write(f"  \u2713 checkpoint saved: {entry.checkpoint_id} ({label})\n")
        else:
            out.write("  no files to track (use within a project directory)\n")
        return "handled"
    elif subcmd == "show":
        cid = parts[1] if len(parts) > 1 else ""
        if not cid:
            out.write("  usage: /checkpoint show <id>\n")
            return "handled"
        cp = cmgr.get_checkpoint(cid)
        if cp is None:
            out.write(f"  checkpoint not found: {cid}\n")
            return "handled"
        out.write(f"  checkpoint: {cp.checkpoint_id}\n")
        out.write(f"    label:     {cp.label}\n")
        out.write(f"    files:     {cp.file_count}\n")
        out.write(f"    size:      {cp.total_bytes} bytes\n")
        out.write(f"    tool_id:   {cp.tool_id}\n")
        out.write(f"    run_id:    {cp.run_id}\n")
        if cp.prev_checkpoint_id:
            out.write(f"    parent:    {cp.prev_checkpoint_id}\n")
        return "handled"
    elif subcmd == "delete":
        cid = parts[1] if len(parts) > 1 else ""
        if not cid:
            out.write("  usage: /checkpoint delete <id>\n")
            return "handled"
        if cmgr.delete_checkpoint(cid):
            out.write(f"  \u2715 checkpoint deleted: {cid}\n")
        else:
            out.write(f"  checkpoint not found: {cid}\n")
        return "handled"
    elif subcmd == "clear":
        count = cmgr.clear_all()
        out.write(f"  \u2715 cleared all {count} checkpoint(s)\n")
        return "handled"
    else:
        out.write("  usage: /checkpoint list|save [name]|show <id>|delete <id>|clear\n")
        return "handled"


@slash_command("/revert", description="restore to a checkpoint", category=_CATEGORY_SESSION)
def cmd_revert(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    cmgr = loop.checkpoint_manager if loop is not None and hasattr(loop, "checkpoint_manager") else None

    if cmgr is not None:
        cps = cmgr.list_checkpoints()
        if not cps:
            out.write("  no checkpoints available\n")
            return "handled"

        target_id = arg.strip() if arg else (cps[0].checkpoint_id if cps else "")
        if not target_id:
            out.write("  no checkpoints to revert\n")
            return "handled"

        cp = cmgr.get_checkpoint(target_id)
        if cp is None:
            out.write(f"  checkpoint not found: {target_id}\n")
            return "handled"

        out.write(f"  checkpoint: {cp.checkpoint_id}\n")
        out.write(f"    label:     {cp.label}\n")
        out.write(f"    files:     {cp.file_count} file(s)\n")
        out.write(f"    size:      {cp.total_bytes} bytes\n")
        if cp.tool_id:
            out.write(f"    trigger:   {cp.tool_id}\n")
        out.write("  reverting...\n")
        out.flush()

        if cmgr.restore_checkpoint(target_id):
            out.write(f"  \u21b6 restored checkpoint: {target_id} ({cp.label})\n")
        else:
            out.write(f"  \u2717 restore failed: {target_id}\n")
        return "handled"

    git_protect = loop.git_protect if loop is not None and hasattr(loop, "git_protect") else None
    if git_protect is not None and hasattr(git_protect, "rollback"):
        try:
            if git_protect.checkpoint_count > 0:
                s = subprocess.run(
                    ["git", "status", "--short"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                )
                if s.stdout.strip():
                    out.write("  will revert:\n")
                    for line in s.stdout.splitlines():
                        out.write(f"    {line}\n")
                git_protect.rollback()
                out.write("  \u21b6 reverted via git checkpoint\n")
            else:
                out.write("  no git checkpoints available\n")
        except Exception as e:
            out.write(f"  \u26a0 git rollback failed: {e}\n")
    else:
        out.write("  no checkpoint system available\n")
    return "handled"




# Extracted from _legacy.py lines 900-1090
# ──────────────────────────────────────────────────────────────────────
# Fix & Review
# ──────────────────────────────────────────────────────────────────────





@slash_command("/fix", description="auto-diagnose and fix last error", category=_CATEGORY_TOOLS)
def cmd_fix(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if loop is None:
        out.write("  start a conversation first (type a message) then /fix\n")
        return "handled"

    last_error = ""
    last_cmd = ""
    recorder = getattr(loop, "recorder", None)
    for ev in reversed(recorder.events):  # type: ignore[union-attr]
        if ev.event_type == EventType.TOOL_CALL_END:
            payload = ev.payload or {}
            tid = payload.get("tool_id", "")
            if tid == "bash" and not payload.get("success", True):
                last_error = payload.get("error", "") or ""
                output = payload.get("output", "") or ""
                for ev2 in reversed(recorder.events):  # type: ignore[union-attr]
                    if (ev2.event_type == EventType.TOOL_CALL_START
                            and ev2.payload.get("tool_id") == "bash"
                            and ev2.payload.get("args", {}).get("command")):
                        last_cmd = ev2.payload["args"]["command"]
                        break
                if not last_cmd and output:
                    last_cmd = output[:200]
                break

    if not last_error and not arg:
        out.write("  (no recent command error found; pass a command to /fix <cmd>)\n")
        return "handled"

    if arg:
        fix_prompt = (
            f"The user ran a command that may have failed. "
            f"Command: {arg}\n"
            f"Please diagnose what might be wrong and suggest a fix."
        )
    else:
        fix_prompt = (
            f"A command we ran failed. Please analyze and fix the issue.\n\n"
            f"Command: {last_cmd}\n"
            f"Error: {last_error}\n\n"
            f"Diagnose the root cause and execute the fix."
        )

    out.write("  analyzing error...\n")
    if last_cmd:
        out.write(f"  command: {last_cmd[:100]}{'...' if len(last_cmd) > 100 else ''}\n")
    if last_error:
        out.write(f"  error: {last_error[:200]}{'...' if len(last_error) > 200 else ''}\n")

    loop.add_user_message(f"/fix: {fix_prompt}")
    out.write("  running auto-fix (up to 5 steps)...\n")
    out.flush()
    _auto_step_loop(loop, out, max_steps=5)
    return "handled"


def _do_second_agent_review(
    out: Any, loop: Any, diff_text: str, diff_files: list[str],
    added_lines: int, deleted_lines: int,
) -> None:
    """v0.5.1: 第二 agent 独立评审。

    使用独立上下文和模型调用, 不污染主对话。
    评审结果直接输出到终端, 不注入到 loop 消息历史。
    """
    if loop is None:
        return
    adapter = getattr(loop, "_model", None)
    if adapter is None:
        out.write("  \u00b7 second-agent review unavailable (no model adapter)\n")
        out.flush()
        return

    from zall.core.model import Message

    # 构建评审上下文
    diff_preview = diff_text[:5000] if diff_text else "(no diff)"
    review_messages = [
        Message(role="system", content=(
            "You are a senior code reviewer. Review the following code changes "
            "and provide a structured analysis. Focus on:\n"
            "1. Bugs & correctness issues\n"
            "2. Security vulnerabilities\n"
            "3. Code quality & style problems\n"
            "4. Missing tests or edge cases\n"
            "5. Suggestions for improvement\n\n"
            "Be concise. Use bullet points. Rate the overall quality: "
            "PASS / MINOR_ISSUES / MAJOR_ISSUES / CRITICAL."
        )),
        Message(role="user", content=(
            f"Code Review Request\n"
            f"===================\n"
            f"Files changed: {len(diff_files)}\n"
            f"Changes: +{added_lines}/-{deleted_lines} lines\n\n"
            f"Files:\n" + "\n".join(f"  - {f}" for f in diff_files[:30]) + "\n\n"
            f"Diff:\n```diff\n{diff_preview}\n```"
        )),
    ]

    try:
        out.write("  \u00b7 second-agent review in progress...\n")
        out.flush()
        resp = adapter.complete(review_messages, tools=[])
        if resp and resp.content:
            out.write(f"\n  {'=' * 40}\n")
            out.write("  \u2192 Second-Agent Review\n")
            out.write(f"  {'=' * 40}\n")
            for line in resp.content.split("\n"):
                out.write(f"  {line}\n")
            out.write(f"  {'=' * 40}\n\n")
        else:
            out.write("  \u00b7 second-agent review returned empty response\n")
    except Exception as e:
        out.write(f"  \u00b7 second-agent review failed: {e}\n")
    out.flush()


@slash_command("/review", description="review uncommitted code changes", category=_CATEGORY_CONTEXT)
def cmd_review(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--git-dir"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
        if r.returncode != 0:
            out.write("  (not a git repository)\n")
            return "handled"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        out.write("  (git unavailable)\n")
        return "handled"

    try:
        staged = subprocess.run(
            ["git", "diff", "--cached", "--stat"],
            capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace",
        )
        staged_names = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace",
        )
        staged_files = set(f.strip() for f in staged_names.stdout.split("\n") if f.strip())
        files_cmd = ["git", "diff", "--name-only"]
        if arg:
            files_cmd = ["git", "diff", "--name-only", "--", arg]
        files_result = subprocess.run(
            files_cmd, capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace",
        )
        diff_files = [f.strip() for f in files_result.stdout.split("\n") if f.strip()]
        if staged_files:
            diff_files = list(dict.fromkeys(diff_files + list(staged_files)))
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        out.write(f"  \u2717 git diff failed: {e}\n")
        return "handled"

    if not diff_files:
        out.write("  (no uncommitted changes)\n")
        return "handled"

    added_lines = 0
    deleted_lines = 0
    diff_stat = staged.stdout.strip() or ""
    for line in diff_stat.split("\n"):
        m = re.search(r"(\d+)\s+insertion", line)
        if m:
            added_lines += int(m.group(1))
        m = re.search(r"(\d+)\s+deletion", line)
        if m:
            deleted_lines += int(m.group(1))

    MAX_REVIEW_FILES = 50
    try:
        diff_cmd = ["git", "diff", "HEAD"]
        if arg:
            diff_cmd = ["git", "diff", "HEAD", "--", arg]
        diff_result = subprocess.run(
            diff_cmd, capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        out.write(f"  \u2717 git diff failed: {e}\n")
        return "handled"

    diff_text = diff_result.stdout or ""

    if hasattr(out, "isatty") and out.isatty():
        c = _shared_console(out)
        c.print()
        c.print(f"[bold cyan]Code Review[/] \u2014 {len(diff_files)} file(s) changed")
        c.print()
        table = Table(title=None, show_header=True,
                      border_style="dim", padding=(0, 1),
                      box=None, collapse_padding=True)
        table.add_column("file", style="cyan", no_wrap=True)
        table.add_column("status", style="dim", width=10)
        table.add_column("changes", style="dim", width=10)
        for f in diff_files[:MAX_REVIEW_FILES]:
            status = "staged" if f in staged_files else "modified"
            table.add_row(f, status, "")
        c.print(table)
        c.print()
        c.print(f"  [dim]+{added_lines}/-{deleted_lines} lines[/]")
        if diff_text:
            try:
                diff_lines = diff_text.split("\n")
                if len(diff_lines) > 200:
                    diff_text = "\n".join(diff_lines[:200])
                    diff_text += f"\n... ({len(diff_lines) - 200} more lines truncated)"
                syntax = Syntax(diff_text, "diff", theme="ansi_dark", line_numbers=False)
                c.print(Panel(syntax, border_style="dim", title="Diff"))
            except Exception:
                c.print(diff_text[:2000])
        c.print()
    else:
        out.write(f"  Code Review: {len(diff_files)} file(s) changed\n")
        out.write(f"  +{added_lines}/-{deleted_lines} lines\n")
        for f in diff_files[:MAX_REVIEW_FILES]:
            out.write(f"    {f}\n")
        if diff_text:
            out.write(diff_text[:2000] + "\n")

    if loop is not None and diff_files:
        summary_lines = "\n".join(f"  - {f}" for f in diff_files[:20])
        review_prompt = (
            f"Please review the following code changes:\n\n"
            f"Files changed ({len(diff_files)}):\n{summary_lines}\n\n"
            f"Diff summary: +{added_lines}/-{deleted_lines} lines\n\n"
            f"```diff\n{diff_text[:3000]}\n```\n\n"
            f"Please review for: bugs, security issues, style problems, "
            f"missing tests, and suggest improvements."
        )
        loop.add_user_message(f"/review: {review_prompt}")
        out.write("  \u2192 review prompt injected for deep analysis\n")

        # v0.5.1: 第二 agent 独立评审 (使用独立上下文, 不污染主对话)
        _do_second_agent_review(out, loop, diff_text, diff_files, added_lines, deleted_lines)
    return "handled"



# Extracted from _legacy.py lines 1592-1660
# ──────────────────────────────────────────────────────────────────────
# Init command
# ──────────────────────────────────────────────────────────────────────

@slash_command("/init", description="initialize project config", category=_CATEGORY_TOOLS)
def cmd_init(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
        try:
            from zall.cli.init_wizard import init_wizard
            init_wizard(out, arg or None)
        except (ImportError, Exception):
            _cmd_init_simple(out)
    else:
        _cmd_init_simple(out)
    return "handled"


# ──────────────────────────────────────────────────────────────────────
# Update command (v2: auto-update mechanism)
# ──────────────────────────────────────────────────────────────────────

@slash_command("/update", description="check and install updates", category=_CATEGORY_NAV)
def cmd_update(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """Check and install zall updates.

    Usage:
        /update          - check and upgrade to latest version
        /update check    - check only, do not upgrade
    """
    from zall.cli.update import check_for_update, get_current_version, perform_update

    current = get_current_version()
    out.write(f"  current: zall {current}\n")
    out.flush()

    if arg.strip().lower() == "check":
        out.write("  checking PyPI for updates...\n")
        out.flush()
        result = check_for_update(force=True)
        if result.get("has_update"):
            out.write(f"  update available: {result['current']} -> {result['latest']}\n")
            out.write("  run /update to install\n")
        else:
            latest = result.get("latest", "unknown")
            out.write(f"  you are on the latest version ({latest})\n")
        return "handled"

    # default: check + 升级
    out.write("  checking for updates...\n")
    out.flush()
    result = check_for_update(force=True)
    if not result.get("has_update"):
        latest = result.get("latest", "unknown")
        out.write(f"  already up to date ({latest})\n")
        return "handled"

    out.write(f"  update available: {result['current']} -> {result['latest']}\n")
    out.flush()

    # execute升级
    success = perform_update(out)
    if success:
        out.write("  restart zall to use the new version\n")
    return "handled"


@slash_command("/forget-permissions", aliases=("/forget-allow",),
              description="clear all persistent always-allow permissions",
              category=_CATEGORY_NAV)
def cmd_forget_permissions(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """E4: Clear all persistent always-allow permissions.

    Removes all tool_ids from the always_allow.json file and clears
    the in-memory allow sets. After this command, all greylist tools
    will prompt for confirmation again.
    """
    from zall.cli.responder import CliUserResponder
    # The responder is accessible via the loop's user_responder attribute
    responder = getattr(loop, "_user_responder", None) if loop else None
    if isinstance(responder, CliUserResponder):
        responder.clear_always_allow()
        out.write("  \u2713 all persistent permissions cleared\n")
    else:
        # Fallback: clear the file directly
        from zall.cli.responder import _always_allow_path
        try:
            path = _always_allow_path()
            if path.exists():
                path.unlink()
            out.write("  \u2713 all persistent permissions cleared\n")
        except Exception as e:
            out.write(f"  \u2717 failed to clear permissions: {e}\n")
    return "handled"


# ──────────────────────────────────────────────────────────────────────
# §12.1 Verifiability: /verify 命令 (第三方独立复核)
# ──────────────────────────────────────────────────────────────────────


def _load_timeline_events(session_dir: Path) -> list[dict] | None:
    """从 session 目录加载 timeline.jsonl, 返回 event dict 列表。

    返回 None 表示 timeline 不存在或无任何可读事件。
    G12: 版本头行自动过滤; 坏行 skip 不再一坏全弃。
    """
    from zall._util.jsonl import read_jsonl
    events = read_jsonl(session_dir / "timeline.jsonl")
    return events if events else None


def _reconstruct_recorder_from_events(run_id: str, events: list[dict]) -> RunRecorder:
    """从 event dict 列表重建 RunRecorder 并验证链完整性。

    IPR-3: stdlib only. 使用 TimelineEvent + RunRecorder.append 重建,
    自动计算 prev_hash 链式关系。
    """
    recorder = RunRecorder(run_id)
    for ev in events:
        recorder.append(
            event_id=ev["event_id"],
            ts=ev["ts"],
            event_type=EventType(ev["event_type"]),
            payload=ev.get("payload", {}),
        )
    return recorder


def _find_latest_session() -> Path | None:
    """返回最近一个 session 的目录路径 (±1s 精度)。"""
    from zall.cli.session import _get_cached_sessions
    sessions = _get_cached_sessions()
    if not sessions:
        return None
    return sessions[0][0]  # 第一个是最近保存的


def _get_anchor_status(session_dir: Path) -> str:
    """检查 session 目录下是否有锚点 ack 事件。

    读取 timeline.jsonl 最后一条 ANCHOR_ACK 事件, 返回状态描述。
    """
    tl_path = session_dir / "timeline.jsonl"
    if not tl_path.exists():
        return "no timeline"
    try:
        last_anchor = None
        for line in tl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if ev.get("event_type") == "anchor_ack":
                    last_anchor = ev
            except json.JSONDecodeError:
                continue
        if last_anchor:
            return (
                f"anchored (anchor_id={last_anchor['payload'].get('anchor_id', '?')[:8]}..., "
                f"sig={last_anchor['payload'].get('sig', '?')[:16]}...)"
            )
        return "not anchored"
    except OSError:
        return "unreadable"


@slash_command("/verify", description="verify timeline chain integrity of a run",
              category=_CATEGORY_TOOLS)
def cmd_verify(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """验证指定 run 的 timeline 链完整性 (§12.1 Verifiability, 第三方独立复核)。

    Usage:
        /verify [run_id]     — 验证指定 run 的 timeline 链完整性
        /verify              — 验证最近一个 session 的 run

    Output:
        - chain: valid | broken
        - event count
        - tail hash (前16位)
        - anchor status (若已锚点)
    """
    if loop is not None and not arg:
        # 当前 loop 中有活跃 recorder, 直接验证
        recorder = loop.recorder
        if recorder.events:
            valid = recorder.verify_chain()
            out.write(f"  verify {recorder.run_id[:16]}...\n")
            out.write(f"    chain:     {'valid' if valid else 'BROKEN'}\n")
            out.write(f"    events:    {len(recorder.events)}\n")
            out.write(f"    tail_hash: {recorder.tail_hash[:16]}...\n")
            out.write(f"    anchored:  {'yes' if any(e.event_type == EventType.ANCHOR_ACK for e in recorder.events) else 'no'}\n")
        else:
            out.write("  (no timeline events recorded in current run)\n")
        return "handled"

    # 从持久化 session 加载
    from zall.cli.session import _get_sessions_dir

    session_dir: Path | None = None
    run_id = arg.strip() if arg else ""

    if run_id:
        # 按 run_id 前缀查找
        sd = _get_sessions_dir()
        if sd.exists():
            for d in sd.iterdir():
                if d.name.startswith(run_id):
                    session_dir = d
                    break
        if session_dir is None:
            out.write(f"  session not found: {run_id}\n")
            return "handled"
    else:
        # 不传 run_id => 用最近一个 session
        latest = _find_latest_session()
        if latest is None:
            out.write("  (no sessions found)\n")
            return "handled"
        session_dir = latest
        run_id = session_dir.name

    events = _load_timeline_events(session_dir)
    if events is None:
        out.write(f"  session {run_id[:16]}... has no timeline.jsonl\n")
        return "handled"

    recorder = _reconstruct_recorder_from_events(run_id, events)
    valid = recorder.verify_chain()
    tail_hash = recorder.tail_hash
    anchor_status = _get_anchor_status(session_dir)

    out.write(f"  verify {run_id[:16]}...\n")
    out.write(f"    chain:     {'valid' if valid else 'BROKEN'}\n")
    out.write(f"    events:    {len(events)}\n")
    out.write(f"    tail_hash: {tail_hash[:16]}...\n")
    out.write(f"    anchor:    {anchor_status}\n")
    return "handled"




@slash_command("/memory", description="show/add/remove cross-session memory (user profile etc.)",
               category=_CATEGORY_SESSION)
def cmd_memory(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """跨会话记忆 CLI 入口 (core.memory.SessionMemory 的交互面)。

    记忆持久化在 ~/.zall/memory.jsonl, 注入每个新会话的 system prompt
    (USER MEMORY 段), 让模型记住用户的身份/偏好/项目知识/错误模式。

    用法:
      /memory                       列出全部记忆 (按类型分组)
      /memory add <text>           添加一条 (默认 user_profile: 身份/偏好)
      /memory add project_knowledge <text>
      /memory add error_patterns <text>
      /memory add decisions <text>
      /memory rm <number>          按序号删除一条
      /memory clear                清空全部记忆

    注意: 新记忆在下一个新会话生效 (当前会话的 system prompt 已固定)。
    """
    from zall.core.memory import MEMORY_TYPES, get_session_memory

    mem = get_session_memory()
    parts = (arg or "").strip().split(None, 1)
    sub = parts[0].lower() if parts else ""

    _labels = {
        "user_profile": "User profile",
        "project_knowledge": "Project knowledge",
        "error_patterns": "Error patterns",
        "decisions": "Decisions",
    }

    def _list() -> None:
        items = mem.list_all()
        if not items:
            out.write('  (no memories yet) — try /memory add "我叫小张, 偏好中文回复"\n')
            return
        for i, m in enumerate(items):
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(m.get("ts", 0)))
            t = _labels.get(str(m.get("type", "?")), str(m.get("type", "?")))
            out.write(f"  [{i}] ({t}) {m.get('content', '')}  [{ts}]\n")
        out.write(f"\n  {len(items)} memory entries — 下一会话注入 system prompt (USER MEMORY)\n")

    if sub == "add":
        body = parts[1].strip() if len(parts) > 1 else ""
        mtype = "user_profile"
        rest = body
        first = body.split(None, 1)[0].strip() if body else ""
        if first in MEMORY_TYPES:
            mtype = first
            rest = body[len(first):].strip()
        if not rest:
            out.write("  usage: /memory add <text>  |  /memory add <type> <text>\n")
            return "handled"
        if mem.add(mtype, rest, source="user"):
            out.write(f"  [+] {mtype}: {rest[:60]}{'...' if len(rest) > 60 else ''}\n")
        else:
            out.write("  could not add memory (invalid type or save failed)\n")
        return "handled"
    if sub == "rm":
        num = parts[1].strip() if len(parts) > 1 else ""
        try:
            idx = int(num)
        except ValueError:
            out.write("  usage: /memory rm <number>\n")
            return "handled"
        items = mem.list_all()
        if idx < 0 or idx >= len(items):
            out.write(f"  no memory at index {idx} (have {len(items)})\n")
            return "handled"
        content = items[idx]["content"]
        if mem.remove(content):
            out.write(f"  ✓ removed: {content}\n")
        else:
            out.write("  remove failed\n")
        return "handled"
    if sub == "clear":
        mem.clear()
        out.write("  ✓ all memories cleared\n")
        return "handled"
    if sub:
        out.write(f"  unknown subcommand '{sub}' (try: add | rm <n> | clear)\n")
        return "handled"
    _list()
    return "handled"
