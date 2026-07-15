"""zall.cli.commands.session — Session lifecycle commands.

Extracted from _legacy.py (v0.2.1 refactor).
Commands: /sessions, /resume, /eval, /replay, /cost, /compact, /undo, /retry

IPR constraints:
  IPR-3: only stdlib + rich, no model SDK
"""

from __future__ import annotations

import time
from typing import Any

from zall._util.model_registry import get_price as _get_model_price
from zall.cli.commands._common import (
    _CATEGORY_SESSION,
    slash_command,
    _recalc_usage_from_timeline,
)
from zall.cli.render import _shared_console
from zall.cli.session import (
    _list_sessions, _run_eval, _run_replay, _run_resume,
    _search_sessions, _tag_session, _prune_sessions,
)
from zall.core.compactor import ModelCompactor
from zall.core.loop import AgentLoop
from zall.core.verifiability import EventType

# Extracted from _legacy.py lines 527-769
@slash_command("/sessions", description="list recent sessions", category=_CATEGORY_SESSION)
def cmd_sessions(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    parts = arg.split()
    subcmd = parts[0] if parts else ""
    if subcmd == "search":
        keyword = " ".join(parts[1:]) if len(parts) > 1 else ""
        _search_sessions(keyword, out)
    elif subcmd == "tag":
        sid = parts[1] if len(parts) > 1 else ""
        tag = parts[2] if len(parts) > 2 else ""
        _tag_session(sid, tag, out)
    elif subcmd == "prune":
        days_str = parts[1] if len(parts) > 1 else ""
        try:
            days = int(days_str) if days_str else 30
        except ValueError:
            days = 30
        _prune_sessions(days, out)
    elif subcmd:
        _list_sessions(out, tag_filter=subcmd)
    else:
        _list_sessions(out)
    return "handled"


@slash_command("/resume", description="resume a session", category=_CATEGORY_SESSION)
def cmd_resume(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if not arg:
        out.write("  usage: /resume <session_id>\n")
        return "handled"
    result = _run_resume(out, arg, state)
    return result if result == "clear" else "handled"


@slash_command("/eval", description="evaluate all sessions", category=_CATEGORY_SESSION)
def cmd_eval(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    _run_eval(out, arg)
    return "handled"


@slash_command("/replay", description="replay a session", category=_CATEGORY_SESSION)
def cmd_replay(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if not arg:
        out.write("  usage: /replay <session_id>\n")
    else:
        _run_replay(out, arg)
    return "handled"


@slash_command("/cost", description="show token usage", category=_CATEGORY_SESSION)
def cmd_cost(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    u = (state or {}).get("usage", {"prompt": 0, "completion": 0})
    total = u["prompt"] + u["completion"]
    model = (state or {}).get("model") or ""
    price_in, price_out = _get_model_price(model)
    cost_in = u["prompt"] * price_in / 1_000_000
    cost_out = u["completion"] * price_out / 1_000_000
    cost = cost_in + cost_out

    if hasattr(out, "isatty") and out.isatty():
        c = _shared_console(out)
        # v0.2.1: table layout for cost display
        from rich.table import Table
        from rich.style import Style
        tbl = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
        tbl.add_column("", style="dim")
        tbl.add_column("", justify="right", style="yellow")
        tbl.add_column("", style="dim")
        tbl.add_column("", justify="right", style="yellow")
        tbl.add_row("model", model or "(default)", "", "")
        tbl.add_row("input", f"{u['prompt']:>8,}", "tokens", f"× ${price_in:.2f}/M → ${cost_in:.4f}")
        tbl.add_row("output", f"{u['completion']:>8,}", "tokens", f"× ${price_out:.2f}/M → ${cost_out:.4f}")
        tbl.add_row("total", f"{total:>8,}", "tokens", f"[bold]${cost:.4f}[/]", style="bold")
        c.print()
        c.print(f"  [cyan]cost[/]")
        c.print(tbl)
    else:
        out.write(f"  cost · model: {model or '(default)'}\n")
        out.write(f"    input {u['prompt']:,} × ${price_in:.2f}/M + output {u['completion']:,} "
                  f"× ${price_out:.2f}/M = ${cost:.4f} (total {total:,} tokens)\n")
    return "handled"


@slash_command("/compact", description="compress conversation context", category=_CATEGORY_SESSION)
def cmd_compact(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if loop is None:
        out.write("  start a conversation first (type a message)\n")
        return "handled"

    msgs = getattr(loop, "messages", getattr(loop, "_messages", []))
    if len(msgs) <= 4:
        out.write("  nothing to compact yet\n")
        return "handled"

    model = getattr(loop, "model_adapter", getattr(loop, "_model", None))
    if model is None:
        out.write("  no model available for compaction\n")
        return "handled"

    compactor = getattr(loop, "compactor", None)
    if compactor is None:
        compactor = ModelCompactor()

    try:
        result = compactor.compact(msgs, model)
    except Exception as e:
        out.write(f"  compaction failed: {e}\n")
        return "handled"

    if result.compacted_count == 0:
        out.write("  nothing to compact yet\n")
        return "handled"

    try:
        recorder = getattr(loop, "recorder", None)
        if recorder:
            step = getattr(loop, "step_count", 0)
            recorder.append(
                event_id=f"compact_{step}",
                ts=int(time.time() * 1000),
                event_type=EventType.CONTEXT_COMPACTION,
                payload={
                    "compacted_count": result.compacted_count,
                    "remaining_count": len(result.compressed_messages),
                    "strategy": result.strategy,
                    "summary": result.summary[:500],
                },
            )
    except Exception:
        pass

    if hasattr(loop, "set_messages"):
        loop.set_messages(list(result.compressed_messages))
    else:
        loop._messages = list(result.compressed_messages)
    if state is not None and "usage" not in state:
        state["usage"] = {"prompt": 0, "completion": 0}

    remaining = len(result.compressed_messages)
    ratio = (result.compacted_count / max(remaining, 1))
    if hasattr(out, "isatty") and out.isatty():
        c = _shared_console(out)
        c.print(f"  [dim]\u23bf compacted: {result.compacted_count} \u2192 {remaining}"
                f" ({ratio:.1f}x compression), timeline preserved[/]")
        if result.summary:
            preview = result.summary[:120]
            c.print(f"    [dim]summary: {preview}{'\u2026' if len(result.summary) > 120 else ''}[/]")
    else:
        out.write(f"  compacted {result.compacted_count} \u2192 {remaining} messages (timeline preserved)\n")
    return "handled"


@slash_command("/undo", description="revert last tool action", category=_CATEGORY_SESSION)
def cmd_undo(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if loop is None:
        out.write("  start a conversation first (type a message)\n")
        return "handled"

    recorder = getattr(loop, "recorder", None)
    if recorder is None:
        out.write("  no recorder available for undo\n")
        return "handled"

    events = list(recorder.events)
    last_tool_end = None
    for ev in reversed(events):
        if ev.event_type == EventType.TOOL_CALL_END:
            last_tool_end = ev
            break

    if last_tool_end is None:
        out.write("  nothing to undo (no tool calls yet)\n")
        return "handled"

    tool_id = last_tool_end.payload.get("tool_id", "?")
    undid_step = last_tool_end.payload.get("step", "?")

    git_protect = getattr(loop, "git_protect", None)
    if git_protect is not None and hasattr(git_protect, "rollback"):
        try:
            if git_protect.checkpoint_count > 0:
                write_tools = getattr(AgentLoop, "WRITE_TOOLS", frozenset({"write_file", "edit_file", "batch_edit", "bash"}))
                last_tool_id = last_tool_end.payload.get("tool_id", "")
                if last_tool_id in write_tools:
                    git_protect.rollback()
                    out.write(f"  \u21b6 file changes reverted via git checkpoint\n")
                else:
                    out.write(f"  \u00b7 last tool ({last_tool_id}) is not a write \u2014 no git rollback needed\n")
        except Exception as e:
            out.write(f"  \u26a0 git rollback failed: {e} (continuing with message undo)\n")

    msgs = list(getattr(loop, "messages", getattr(loop, "_messages", [])))
    last_tool_result_idx = -1
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].role == "tool":
            last_tool_result_idx = i
            break

    if last_tool_result_idx >= 0:
        assistant_idx = -1
        for i in range(last_tool_result_idx - 1, -1, -1):
            if msgs[i].role == "assistant":
                assistant_idx = i
                break
        if assistant_idx >= 0:
            new_msgs = msgs[:assistant_idx]
            loop.set_messages(new_msgs)
            out.write(f"  \u21b6 undid: {tool_id} (step {undid_step})\n")
            _recalc_usage_from_timeline(recorder, state)
            return "handled"

    out.write("  nothing to undo (context structure not suitable)\n")
    return "handled"


@slash_command("/retry", description="re-do last agent step", category=_CATEGORY_SESSION)
def cmd_retry(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if loop is None:
        out.write("  start a conversation first (type a message) then /retry\n")
        return "handled"

    msgs = getattr(loop, "messages", getattr(loop, "_messages", []))
    last_assistant_idx = -1
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].role == "assistant":
            last_assistant_idx = i
            break

    if last_assistant_idx < 0:
        out.write("  (no assistant message to retry)\n")
        return "handled"

    remove_from = last_assistant_idx
    removed_count = len(msgs) - remove_from
    new_msgs = msgs[:remove_from]

    if hasattr(loop, "set_messages"):
        loop.set_messages(new_msgs)
    else:
        loop._messages = new_msgs
    out.write(f"  \u2713 removed last response ({removed_count} message(s)), retrying...\n")
    return "handled"


# ──────────────────────────────────────────────────────────────────────
# /remember, /forget — project memory commands (v0.4.0)
# ──────────────────────────────────────────────────────────────────────


@slash_command("/remember", description="remember convention/key: value in AGENTS.md", category=_CATEGORY_SESSION)
def cmd_remember(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """记住项目约定到 AGENTS.md。

    Usage:
        /remember <key: value>         — 添加/更新项目记忆
        /remember conventions: use f-strings
        /remember test: pytest
        /remember build: pip install -e .
        /remember note: this project uses FastAPI
    """
    if not arg or ":" not in arg:
        out.write("  usage: /remember <key>: <value>\n")
        out.write("  supported keys: conventions, test, build, run, note, deploy\n")
        out.write("  example: /remember conventions: use f-strings\n")
        return "handled"

    # parse key: value
    colon_idx = arg.index(":")
    key = arg[:colon_idx].strip().lower()
    value = arg[colon_idx + 1:].strip()
    if not key or not value:
        out.write("  both key and value are required\n")
        return "handled"

    # 支持的 key 到 AGENTS.md 章节mapping
    section_map = {
        "conventions": "## Conventions",
        "test": "## Common Commands",
        "build": "## Common Commands",
        "run": "## Common Commands",
        "deploy": "## Common Commands",
        "note": "## Project Overview",
        "notes": "## Project Overview",
    }

    from pathlib import Path
    agents_path = Path.cwd() / ".zall" / "AGENTS.md"

    if not agents_path.parent.exists():
        agents_path.parent.mkdir(parents=True, exist_ok=True)

    if agents_path.exists():
        content = agents_path.read_text(encoding="utf-8")
    else:
        from zall.cli.commands._common import _generate_agents_md
        content = _generate_agents_md(str(Path.cwd()))

    # construct要add的条目
    section = section_map.get(key, "## Project Overview")
    entry = f"- {key}: {value}"

    if section in content:
        # 在corresponds to section 下add, 如果该 key 已存在则replace
        lines = content.split("\n")
        in_section = False
        section_found = False
        replaced = False
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped == section:
                in_section = True
                section_found = True
            elif stripped.startswith("## ") and in_section and stripped != section:
                # 下一个 section, 追加到当前 section 末尾
                if not replaced:
                    new_lines.append(entry)
                    replaced = True
                in_section = False
            elif in_section and stripped.startswith(f"- {key}:"):
                new_lines.append(entry)
                replaced = True
                continue
            new_lines.append(line)
        if in_section and not replaced:
            new_lines.append(entry)
        content = "\n".join(new_lines)

        if replaced:
            out.write(f"  ✓ updated {key} in AGENTS.md\n")
        else:
            out.write(f"  ✓ added {key} to AGENTS.md\n")
    else:
        # section 不存在, 追加到file末尾
        content += f"\n{section}\n{entry}\n"
        out.write(f"  ✓ created section '{section}' with {key}\n")

    agents_path.write_text(content, encoding="utf-8")

    # 如果 loop 中有 system prompt, 尝试热更新
    if loop is not None and hasattr(loop, "messages") and hasattr(loop, "set_messages"):
        try:
            msgs = list(loop.messages)
            for i, m in enumerate(msgs):
                if m.role == "system" and "PROJECT MEMORY" in (m.content or ""):
                    from zall.cli.environment import read_agents_md
                    cwd = str(Path.cwd())
                    agents_content = read_agents_md(cwd)
                    if agents_content:
                        updated_sys = m.content
                        # replace PROJECT MEMORY 块
                        import re
                        updated_sys = re.sub(
                            r'PROJECT MEMORY \(from \.zall/AGENTS\.md[^)]*\):.*?(?=\n\n|\Z)',
                            f'PROJECT MEMORY (from .zall/AGENTS.md -- project conventions, read-only):\n{agents_content}',
                            updated_sys,
                            flags=re.DOTALL,
                        )
                        if updated_sys != m.content:
                            from zall.core.model import Message
                            msgs[i] = Message(role="system", content=updated_sys)
                            loop.set_messages(msgs)
                            out.write("  ✓ hot-updated system prompt with new memory\n")
                    break
        except Exception:
            pass

    return "handled"


@slash_command("/forget", description="clear AGENTS.md to template", category=_CATEGORY_SESSION)
def cmd_forget(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """清空 AGENTS.md 重置为inittemplate。

    Usage:
        /forget — 重置 AGENTS.md（需要确认）
    """
    from pathlib import Path
    agents_path = Path.cwd() / ".zall" / "AGENTS.md"
    if not agents_path.exists():
        out.write("  (no AGENTS.md to forget)\n")
        return "handled"

    out.write("  ⚠ This will reset AGENTS.md to the default template.\n")
    out.write("  Type 'yes' to confirm: ")
    out.flush()
    try:
        input_fn = state.get("_input_fn") if state else None
        if input_fn is None:
            input_fn = input
        answer = input_fn().strip().lower()
    except (EOFError, KeyboardInterrupt):
        out.write("\n  cancelled\n")
        return "handled"

    if answer not in ("yes", "y"):
        out.write("  cancelled\n")
        return "handled"

    from zall.cli.commands._common import _generate_agents_md
    agents_path.write_text(_generate_agents_md(str(Path.cwd())), encoding="utf-8")
    out.write("  ✓ AGENTS.md reset to template\n")

    # 也尝试热更新 loop system prompt
    if loop is not None and hasattr(loop, "set_messages"):
        try:
            msgs = list(loop.messages)
            for i, m in enumerate(msgs):
                if m.role == "system" and "PROJECT MEMORY" in (m.content or ""):
                    from zall.cli.environment import read_agents_md
                    cwd = str(Path.cwd())
                    agents_content = read_agents_md(cwd)
                    if agents_content:
                        import re
                        updated_sys = re.sub(
                            r'PROJECT MEMORY \(from \.zall/AGENTS\.md[^)]*\):.*?(?=\n\n|\Z)',
                            f'PROJECT MEMORY (from .zall/AGENTS.md -- project conventions, read-only):\n{agents_content}',
                            m.content,
                            flags=re.DOTALL,
                        )
                        if updated_sys != m.content:
                            from zall.core.model import Message
                            msgs = list(loop.messages)
                            msgs[i] = Message(role="system", content=updated_sys)
                            loop.set_messages(msgs)
                            out.write("  ✓ system prompt updated\n")
                    break
        except Exception:
            pass

    return "handled"

