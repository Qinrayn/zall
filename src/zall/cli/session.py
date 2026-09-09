"""CLI session management — save/load/list/search/tag/prune/eval/replay/resume.

Extracted from cli/app.py (v0.1.1 refactor).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from rich.table import Table

from zall.cli.render import _shared_console


def _home_dir() -> Path:
    """Get the user home directory, robust on Windows with non-ASCII usernames.

    Path.home() can fail or return wrong paths when the username contains
    non-ASCII characters (e.g., Chinese) on Windows. Falls back to
    the USERPROFILE environment variable.
    """
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


def _get_sessions_dir() -> Path:
    """Get the sessions directory path."""
    return _home_dir() / ".zall" / "sessions"


# O5: session list cache with mtime invalidation
_SESSIONS_CACHE: dict[str, Any] = {"mtime": 0.0, "entries": []}


def reset_sessions_cache() -> None:
    """v0.5.0: 重置会话缓存, 用于测试隔离。"""
    _SESSIONS_CACHE["mtime"] = 0.0
    _SESSIONS_CACHE["entries"] = []


# ── Autosave (crash recovery) ──

_REPL_AUTOSAVE = _home_dir() / ".zall" / ".repl_autosave.json"
_AUTOSAVE_FRESH_SECONDS = 60


def _is_pid_alive(pid: int) -> bool:
    """Check if a PID is still running (cross-platform)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _save_repl_state(loop: Any, state: dict[str, Any]) -> None:
    """Auto-save REPL conversation state for crash recovery.

    E4: Fixed filename (no PID), atomic write via tmp + os.replace,
    soft lock via PID field in JSON content.
    """
    try:
        _REPL_AUTOSAVE.parent.mkdir(parents=True, exist_ok=True)
        msgs = loop.messages
        data = {
            "model": state.get("model") or "",
            "verbose": state.get("verbose", False),
            "usage": state.get("usage", {"prompt": 0, "completion": 0}),
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "tool_call_id": m.tool_call_id,
                    "tool_id": getattr(m, "tool_id", None),
                    "tool_calls": [
                        {"id": tc.id, "tool_id": tc.tool_id, "args": dict(tc.args)}
                        for tc in m.tool_calls
                    ],
                }
                for m in msgs
            ],
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "pid": os.getpid(),
        }
        # Atomic write: write to .tmp then os.replace
        tmp_path = _REPL_AUTOSAVE.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(str(tmp_path), str(_REPL_AUTOSAVE))
    except Exception:
        pass


def _clear_repl_autosave() -> None:
    """Clear the autosave file (called on clean exit)."""
    try:
        if _REPL_AUTOSAVE.exists():
            _REPL_AUTOSAVE.unlink()
        # Clean up any residual .tmp file
        tmp_path = _REPL_AUTOSAVE.with_suffix(".json.tmp")
        if tmp_path.exists():
            tmp_path.unlink()
    except Exception:
        pass


def _sweep_legacy_autosaves() -> int:
    """一次性清扫旧版 PID 命名的 autosave 残留 (.repl_autosave_<pid>.json)。

    E4 前的版本每个进程写一个 PID 命名文件, 崩溃/强杀后无人回收,
    磁盘垃圾无限累积。仅删 PID 已死的文件 — 若旧版本进程仍在运行,
    其 autosave 不动 (与 E4 软锁同一保护语义)。返回删除数。
    """
    removed = 0
    try:
        for f in _REPL_AUTOSAVE.parent.glob(".repl_autosave_*.json"):
            suffix = f.name[len(".repl_autosave_"):-len(".json")]
            if not suffix.isdigit():
                continue
            if _is_pid_alive(int(suffix)):
                continue
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    except Exception:
        pass
    return removed


def _check_repl_autosave(out: Any, state: dict[str, Any]) -> bool:
    """Check for crash-recovery session and prompt user to restore.

    E4: Uses fixed filename `.repl_autosave.json`. Reads PID from JSON
    to determine if the autosave belongs to a currently running process.
    If the owning process is still alive and different from current, the
    autosave is left untouched (another zall is running).
    """
    _sweep_legacy_autosaves()  # 顺手回收旧版 PID 命名残留 (仅死进程)
    if not _REPL_AUTOSAVE.exists():
        return False
    try:
        data = json.loads(_REPL_AUTOSAVE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    msgs_raw = data.get("messages", [])
    if not msgs_raw:
        return False
    saved_at = data.get("saved_at", "unknown")
    msg_count = len(msgs_raw)
    model_label = data.get("model", "?")
    saved_pid = data.get("pid", 0)

    # E4: soft lock — if PID belongs to another alive process, skip
    if saved_pid and saved_pid != os.getpid() and _is_pid_alive(saved_pid):
        # Another zall process is running; leave its autosave alone
        return False

    if not hasattr(out, "isatty") or not out.isatty():
        # Non-TTY: auto-clean old autosaves from dead processes
        if saved_pid and not _is_pid_alive(saved_pid):
            _clear_repl_autosave()
        return False
    # Build a rich recovery message with count, time, and last message summary
    last_message = msgs_raw[-1] if msgs_raw else {}
    last_role = last_message.get("role", "")
    last_content = last_message.get("content", "")
    last_summary = ""
    if last_content:
        trimmed = last_content[:50]
        if len(last_content) > 50:
            trimmed += "..."
        last_summary = f"  last: [{last_role}] {trimmed}"

    out.write(f"  ! previous REPL session saved at {saved_at[:16]} "
              f"({msg_count} messages, model: {model_label})\n")
    if last_summary:
        out.write(f"  {last_summary}\n")
    out.flush()
    ask = state.get("_input_fn") or input
    try:
        ans = ask("  restore? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = ""
    if ans not in ("y", "yes"):
        _clear_repl_autosave()
        return False
    from zall.core.model import Message as _Msg
    from zall.core.model import ToolCall as _ToolCall
    msgs = []
    for m in msgs_raw:
        tool_calls = tuple(
            _ToolCall(id=tc.get("id", ""), tool_id=tc.get("tool_id", ""), args=tc.get("args", {}))
            for tc in m.get("tool_calls", [])
        )
        msgs.append(_Msg(
            role=m.get("role", "user"),
            content=m.get("content", ""),
            tool_call_id=m.get("tool_call_id"),
            tool_id=m.get("tool_id") or "",
            tool_calls=tool_calls,
        ))
    state["resume_messages"] = msgs
    state["usage"] = data.get("usage", {"prompt": 0, "completion": 0})
    if data.get("model"):
        state["model"] = data["model"]
    _clear_repl_autosave()
    out.write(f"  + restored {len(msgs)} messages\n")
    out.flush()
    return True


# ── Session persistence ──


def _save_session(run_id: str, loop: Any, egress: Any, anchor: Any = None) -> Path:
    """Save RunRecorder timeline + messages.json + meta (with project memory snapshot)."""
    import hashlib
    d = _get_sessions_dir() / run_id
    d.mkdir(parents=True, exist_ok=True)

    if anchor is not None:
        import time as _time
        try:
            loop.recorder.anchor_to(anchor, int(_time.time() * 1000))
        except Exception:
            pass

    timeline_path = d / "timeline.jsonl"
    messages_path = d / "messages.json"

    # M-fix: 流式写出 (spill 模式下 recorder.events 物化全量会顶一次
    # 内存峰值 — iter_events 让盘头 + 内存尾逐条流出, 不额外驻留)。
    events_stream = getattr(loop.recorder, "iter_events", lambda: ())
    with open(timeline_path, "w", encoding="utf-8") as f_tl:
        # G12: 首行版本头 (无头旧文件视为 legacy, 读取端向后兼容)
        from zall._util.jsonl import make_metadata
        f_tl.write(json.dumps(
            make_metadata(run_id=run_id, saved_at=datetime.now().isoformat(timespec="seconds")),
            ensure_ascii=False,
        ) + "\n")
        for ev in events_stream():
            f_tl.write(json.dumps({
                "event_id": ev.event_id,
                "ts": ev.ts,
                "event_type": ev.event_type.value,
                "payload": ev.payload,
                "prev_hash": ev.prev_hash,
                "hash": ev.compute_hash(),
            }, ensure_ascii=False) + "\n")
    # 释放 spill 文件句柄 (幂等; 关闭后 timeline.jsonl 已完整)
    close_fn = getattr(loop.recorder, "close", None)
    if close_fn is not None:
        close_fn()
    if getattr(loop.recorder, "spill_path", None) is not None:
        try:
            getattr(loop.recorder, "spill_path").unlink(missing_ok=True)
        except OSError:
            pass  # spill 文件与 timeline.jsonl 内容重复; 删除失败不阻塞

    msgs_serialized = []
    for m in loop.messages:
        msgs_serialized.append({
            "role": m.role,
            "content": m.content,
            "tool_call_id": m.tool_call_id,
            "tool_id": m.tool_id,
            "tool_calls": [
                {"id": tc.id, "tool_id": tc.tool_id, "args": dict(tc.args)}
                for tc in m.tool_calls
            ],
        })
    messages_path.write_text(
        json.dumps(msgs_serialized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Read AGENTS.md snapshot hash
    agents_md_hash = ""
    try:
        agents_path = Path.cwd() / ".zall" / "AGENTS.md"
        if agents_path.exists():
            agents_md_hash = hashlib.sha256(
                agents_path.read_bytes()
            ).hexdigest()[:16]
    except Exception:
        pass

    # Get git HEAD SHA
    git_sha = ""
    if hasattr(loop, "_resolve_git_sha"):
        try:
            git_sha = loop._resolve_git_sha("HEAD") or ""
        except Exception:
            pass

    with open(d / "meta.json", "w", encoding="utf-8") as f:
        json.dump({
            "run_id": run_id,
            "final_state": egress.final_state.value,
            "step_count": egress.step_count,
            "model_calls": egress.total_model_calls,
            "tool_calls": egress.total_tool_calls,
            "error": egress.error,
            "tags": [],
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "project_root": str(Path.cwd()),
            "agents_md_hash": agents_md_hash,
            "git_head_sha": git_sha,
        }, f, ensure_ascii=False, indent=2)
    return d


def _load_session_messages(session_dir: Path) -> tuple[list[Any] | None, dict[str, Any] | None]:
    """Load messages.json + meta.json from a session directory."""
    path = session_dir / "messages.json"
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, None
    from zall.core.model import Message, ToolCall
    msgs: list[Any] = []
    for m in data:
        tool_calls = tuple(
            ToolCall(id=tc["id"], tool_id=tc["tool_id"], args=tc.get("args", {}))
            for tc in m.get("tool_calls", [])
        )
        msgs.append(Message(
            role=m["role"],
            content=m.get("content", ""),
            tool_call_id=m.get("tool_call_id"),
            tool_id=m.get("tool_id", ""),
            tool_calls=tool_calls,
        ))
    # Read meta
    meta = None
    meta_path = session_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return msgs, meta


# ── Session listing / search / tag / prune ──


def _get_cached_sessions() -> list[tuple[Path, dict[str, Any]]]:
    """O5: Return session metadata list, cached with mtime invalidation."""
    if not _get_sessions_dir().exists():
        return []
    try:
        current_mtime = _get_sessions_dir().stat().st_mtime
    except OSError:
        return []
    if current_mtime == _SESSIONS_CACHE.get("mtime", 0):
        return list(_SESSIONS_CACHE["entries"])
    entries: list[tuple[Path, dict[str, Any]]] = []
    for d in _get_sessions_dir().iterdir():
        meta = d / "meta.json"
        if meta.exists():
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                entries.append((d, data))
            except (json.JSONDecodeError, OSError):
                continue
    entries.sort(key=lambda x: x[1].get("saved_at", ""), reverse=True)
    _SESSIONS_CACHE["mtime"] = current_mtime
    _SESSIONS_CACHE["entries"] = entries[:200]
    return list(_SESSIONS_CACHE["entries"])


def _list_sessions(out: Any, tag_filter: str = "") -> None:
    """List recent sessions (rich Table for TTY, plain text for non-TTY)."""
    all_entries = _get_cached_sessions()
    if not all_entries:
        out.write("  (no sessions)\n")
        return
    if tag_filter:
        entries = [(d, data) for d, data in all_entries
                    if tag_filter in data.get("tags", [])]
    else:
        entries = list(all_entries)

    if hasattr(out, "isatty") and out.isatty():
        console = _shared_console(out)
        table = Table(title=f"sessions ({len(entries)})",
                      show_header=True, header_style="cyan",
                      border_style="dim", padding=(0, 1), expand=False)
        table.add_column("id", style="cyan", no_wrap=True)
        table.add_column("state", no_wrap=True)
        table.add_column("steps", justify="right")
        table.add_column("tools", justify="right")
        table.add_column("tags", style="dim")
        table.add_column("saved", style="dim")
        for d, data in entries[:10]:
            state = data.get("final_state", "?")
            steps = data.get("step_count", "?")
            tools = data.get("tool_calls", "?")
            tags = ", ".join(data.get("tags", [])) or "-"
            saved = data.get("saved_at", "-")
            if isinstance(saved, str) and len(saved) > 16:
                saved = saved[:16]
            icon = {"met": "+", "not_met": "+", "undecidable": "o"}.get(state, ".")
            st_color = {"met": "green", "not_met": "red",
                        "undecidable": "yellow"}.get(state, "dim")
            table.add_row(d.name[:8], f"[{st_color}]{icon} {state}[/]",
                          str(steps), str(tools), tags, saved)
        console.print(table)
        if tag_filter:
            console.print(f"  [dim]filtered by tag: {tag_filter}[/]")
        console.print("  [dim]/sessions search <keyword>  -  search by content[/]")
        console.print("  [dim]/sessions tag <id> <tag>    -  add tag to session[/]")
        console.print("  [dim]/sessions prune [days]      -  delete old sessions[/]")
    else:
        out.write(f"  sessions ({len(entries)}):\n")
        for d, data in entries[:10]:
            state = data.get("final_state", "?")
            steps = data.get("step_count", "?")
            tags = ", ".join(data.get("tags", [])) or "-"
            saved = data.get("saved_at", "-")
            icon = {"met": "+", "not_met": "+", "undecidable": "o"}.get(state, ".")
            out.write(f"    {icon} {d.name[:8]} - {state} - {steps} steps - [{tags}] - {saved}\n")


def _search_sessions(keyword: str, out: Any) -> None:
    """Search sessions by content keyword."""
    all_entries = _get_cached_sessions()
    if not all_entries:
        out.write("  (no sessions)\n")
        return
    if not keyword:
        out.write("  usage: /sessions search <keyword>\n")
        return

    MAX_RESULTS = 15
    results: list[tuple[str, str, str]] = []
    skipped_large = 0
    for d, _ in all_entries:
        if len(results) >= MAX_RESULTS:
            break
        session_id = d.name[:8]
        msgs_path = d / "messages.json"
        if msgs_path.exists():
            try:
                size = msgs_path.stat().st_size
                if size > 5_000_000:
                    skipped_large += 1
                    continue
                text = msgs_path.read_text(encoding="utf-8")
                if keyword.lower() in text.lower():
                    idx = text.lower().find(keyword.lower())
                    start = max(0, idx - 40)
                    end = min(len(text), idx + 40)
                    preview = text[start:end].replace("\n", " ")
                    results.append((session_id, "messages", preview[:80]))
                    continue
            except (OSError, json.JSONDecodeError):
                pass
        meta_path = d / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                tags = meta.get("tags", [])
                if keyword.lower() in [t.lower() for t in tags]:
                    results.append((session_id, "tags", f"tagged: {keyword}"))
            except (OSError, json.JSONDecodeError):
                pass

    if skipped_large > 0:
        results.append(("", "warn", f"({skipped_large} session(s) >5MB, skipped)"))

    if not results:
        out.write(f"  no sessions match '{keyword}'\n")
        return
    out.write(f"  found {len(results)} session(s) matching '{keyword}':\n")
    for sid, src, preview in results[:MAX_RESULTS]:
        out.write(f"    {sid} - [{src}] {preview}\n")


def _tag_session(session_id: str, tag: str, out: Any) -> None:
    """Add a tag to a session."""
    if not _get_sessions_dir().exists():
        out.write("  (no sessions)\n")
        return
    if not session_id or not tag:
        out.write("  usage: /sessions tag <id> <tag>\n")
        return
    target = None
    for d in _get_sessions_dir().iterdir():
        if d.name.startswith(session_id):
            target = d
            break
    if target is None:
        out.write(f"  session not found: {session_id}\n")
        return
    meta_path = target / "meta.json"
    if not meta_path.exists():
        out.write(f"  session {target.name[:8]} has no metadata\n")
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        out.write(f"  cannot read metadata for {target.name[:8]}\n")
        return
    tags = meta.get("tags", [])
    if tag not in tags:
        tags.append(tag)
    meta["tags"] = tags
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    # 失效缓存 (修复: 子目录文件变更不改变父目录 mtime, 缓存不会自动失效)
    _SESSIONS_CACHE.pop("mtime", None)
    out.write(f"  + tagged {target.name[:8]} with '{tag}'\n")


def _prune_sessions(days: int, out: Any) -> None:
    """Delete sessions older than the specified number of days.

    Keeps at least 5 most recent sessions regardless of age.
    Sessions older than _MAX_ABSOLUTE_AGE_DAYS are deleted even if <5 total.
    Only deletes complete session directories (with meta.json).
    """
    from datetime import datetime

    _MAX_ABSOLUTE_AGE_DAYS = 365

    if not _get_sessions_dir().exists():
        out.write("  (no sessions)\n")
        return

    if days <= 0:
        out.write("  usage: /sessions prune <days>  (delete sessions older than N days)\n")
        return

    cutoff = datetime.now() - timedelta(days=days)
    abs_cutoff = datetime.now() - timedelta(days=_MAX_ABSOLUTE_AGE_DAYS)
    entries: list[tuple[Path, dict[str, Any]]] = []
    for d in _get_sessions_dir().iterdir():
        meta = d / "meta.json"
        if meta.exists():
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                entries.append((d, data))
            except (json.JSONDecodeError, OSError):
                continue

    entries.sort(key=lambda x: x[1].get("saved_at", ""), reverse=True)

    if len(entries) <= 5:
        all_old = True
        for d, data in entries:
            saved_str = data.get("saved_at", "")
            try:
                saved_time = datetime.fromisoformat(saved_str[:19])
            except (ValueError, TypeError):
                continue
            if saved_time >= abs_cutoff:
                all_old = False
                break
        if not all_old:
            out.write("  only 5 sessions or fewer, nothing to prune\n")
            return

    deleted = 0
    for d, data in reversed(entries):
        saved_str = data.get("saved_at", "")
        try:
            saved_time = datetime.fromisoformat(saved_str[:19])
        except (ValueError, TypeError):
            continue

        is_in_top5 = False
        for i in range(min(5, len(entries))):
            if entries[i][0] == d:
                is_in_top5 = True
                break

        if is_in_top5 and saved_time >= abs_cutoff:
            continue

        if saved_time < cutoff or saved_time < abs_cutoff:
            import shutil
            try:
                shutil.rmtree(d)
                deleted += 1
            except OSError:
                pass

    if deleted > 0:
        out.write(f"  + pruned {deleted} session(s) older than {days} day(s)\n")
    else:
        out.write(f"  no sessions older than {days} day(s) to prune\n")


# ── Eval / Replay / Resume ──


def _run_eval(out: Any, session_filter: str = "") -> None:
    """/eval: Evaluate sessions."""
    from zall.eval.metrics import evaluate, load_all_sessions
    sessions = load_all_sessions(base_dir=_get_sessions_dir())
    if session_filter:
        sessions = [s for s in sessions if session_filter in s.run_id]
    if not sessions:
        out.write("  (no sessions to evaluate)\n")
        return
    report = evaluate(sessions)
    health_color = {"healthy": "green", "warning": "yellow", "critical": "red"}.get(
        report.health, "dim")
    if hasattr(out, "isatty") and out.isatty():
        console = _shared_console(out)
        console.print(f"\n  eval - {len(sessions)} session(s) - health: "
                      f"[{health_color}]{report.health}[/]")
        table = Table(show_header=True, header_style="cyan", border_style="dim",
                      padding=(0, 1), expand=False)
        table.add_column("metric", style="dim", no_wrap=True)
        table.add_column("value", style="yellow", justify="right")
        table.add_column("anti-metric", style="dim", no_wrap=True)
        table.add_column("anti-value", style="red", justify="right")
        table.add_column("notes", style="dim")
        for m in report.metrics:
            v_color = "green" if m.value > 0.5 else ("yellow" if m.value > 0.2 else "red")
            a_color = "red" if m.anti_value > 0.3 else "green"
            table.add_row(
                m.name,
                f"[{v_color}]{m.value:.0%}[/]",
                m.anti_name,
                f"[{a_color}]{m.anti_value:.0%}[/]",
                "\n".join(m.notes) if m.notes else "",
            )
        console.print(table)
    else:
        out.write(f"\n  eval - {len(sessions)} session(s) - health: {report.health}\n")
        out.write("  " + "-" * 60 + "\n")
        for m in report.metrics:
            out.write(f"  {m.name:25s} {m.value:6.0%}  |  {m.anti_name:25s} {m.anti_value:6.0%}\n")
            for n in m.notes:
                out.write(f"    {n}\n")
        out.write("  " + "-" * 60 + "\n")


def _run_replay(out: Any, session_id: str) -> None:
    """/replay <id>: Replay a session for reproducibility verification."""
    from zall.cli.replay import compare_egress, replay_session
    if not _get_sessions_dir().exists():
        out.write("  (no sessions)\n")
        return
    target = None
    for d in _get_sessions_dir().iterdir():
        if d.name.startswith(session_id):
            target = d
            break
    if target is None:
        out.write(f"  session not found: {session_id}\n")
        return
    out.write(f"  replaying {target.name[:16]}...\n")
    result = replay_session(target)
    if result is None:
        out.write("  session incomplete (no timeline/meta)\n")
        return
    egress, meta = result
    cmp = compare_egress(egress, meta)
    if cmp["reproduced"]:
        out.write(f"  + reproduced - steps {cmp['replayed_steps']} - tools {cmp['replayed_tools']}\n")
    else:
        out.write(f"  x DIVERGENT - steps {cmp['replayed_steps']}/{cmp['original_steps']}"
                  f" - tools {cmp['replayed_tools']}/{cmp['original_tools']}\n")
    out.write(f"    replay state: {cmp['replayed_state']} | original: {cmp['original_state']}\n")


def _run_resume(out: Any, session_id: str, state: dict[str, Any] | None) -> str | None:
    """/resume <id>: Restore session context into REPL.

    Re-reads current AGENTS.md; if its hash differs from the saved one,
    injects an update notification message.
    """
    import hashlib
    if not _get_sessions_dir().exists():
        out.write("  (no sessions)\n")
        return None
    if state is None:
        out.write("  (no REPL state)\n")
        return None
    target = None
    for d in _get_sessions_dir().iterdir():
        if d.name.startswith(session_id):
            target = d
            break
    if target is None:
        out.write(f"  session not found: {session_id}\n")
        return None

    msgs, meta = _load_session_messages(target)
    if msgs is None:
        out.write(f"  session {target.name[:8]} has no restorable messages "
                  f"(older format); try /replay {target.name[:8]}\n")
        return None

    from zall.core.model import Message

    # Check if AGENTS.md has changed
    current_agents_md = ""
    agents_changed = False
    try:
        agents_path = Path.cwd() / ".zall" / "AGENTS.md"
        if agents_path.exists():
            current_agents_md = agents_path.read_text(encoding="utf-8").strip()
            current_hash = hashlib.sha256(current_agents_md.encode("utf-8")).hexdigest()[:16]
            saved_hash = (meta or {}).get("agents_md_hash", "")
            if saved_hash and current_hash != saved_hash:
                agents_changed = True
    except Exception:
        pass

    if agents_changed and current_agents_md:
        first_line = current_agents_md.split("\n")[0][:80] if current_agents_md else ""
        note = Message(role="system",
                       content=(
                           f"[resumed from session {target.name[:8]}, "
                           f"user explicit]\n"
                           f"[UPDATED PROJECT MEMORY - AGENTS.md has changed since this "
                           f"session was saved]\n"
                           f"[Current AGENTS.md: {first_line}]"
                       ))
        msgs.append(note)
        out.write("  ! AGENTS.md has changed since session was saved\n")
        out.write("  injected current project memory\n")
    else:
        note = Message(role="system",
                       content=f"[resumed from session {target.name[:8]}, user explicit]")
        msgs.append(note)

    state["resume_messages"] = msgs
    out.write(f"  + resumed {target.name[:8]} - {len(msgs)} messages loaded\n")
    out.write("  next input continues this context (type /clear to discard)\n")
    return "clear"
