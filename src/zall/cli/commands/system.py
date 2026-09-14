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

from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

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
from zall.cli.render import _shared_console
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


@slash_command("/clear", description="clear screen", category=_CATEGORY_NAV)
def cmd_clear(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
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


