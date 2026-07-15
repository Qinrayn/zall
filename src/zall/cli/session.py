"""CLI session management — save/load/list/search/tag/prune/eval/replay/resume.

Extracted from cli/app.py (v0.1.1 refactor).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from rich.table import Table

from zall.cli.render import _shared_console
from zall.core.goal import TerminationState


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


# ── Autosave (crash recovery) ──

_REPL_AUTOSAVE = _home_dir() / ".zall" / f".repl_autosave_{os.getpid()}.json"


def _save_repl_state(loop: Any, state: dict[str, Any]) -> None:
    """Auto-save REPL conversation state for crash recovery."""
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
                    "tool_calls": [
                        {"id": tc.id, "tool_id": tc.tool_id, "args": dict(tc.args)}
                        for tc in m.tool_calls
                    ],
                }
                for m in msgs
            ],
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        _REPL_AUTOSAVE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _clear_repl_autosave() -> None:
    """Clear the autosave file (called on clean exit)."""
    try:
        if _REPL_AUTOSAVE.exists():
            _REPL_AUTOSAVE.unlink()
    except Exception:
        pass


def _check_repl_autosave(out: Any, state: dict[str, Any]) -> bool:
    """Check for crash-recovery session and prompt user to restore."""
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
    if not hasattr(out, "isatty") or not out.isatty():
        return False
    out.write(f"  ! previous REPL session saved at {saved_at[:16]} "
              f"({msg_count} messages, model: {model_label})\n")
    out.flush()
    ask = state.get("_input_fn") or input
    try:
        ans = ask("  restore? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = ""
    if ans not in ("y", "yes"):
        _clear_repl_autosave()
        return False
    from zall.core.model import ToolCall as _ToolCall, Message as _Msg
    msgs = []
    for m in msgs_raw:
        tool_calls = tuple(
            _ToolCall(id=tc["id"], tool_id=tc["tool_id"], args=tc.get("args", {}))
            for tc in m.get("tool_calls", [])
        )
        msgs.append(_Msg(
            role=m["role"],
            content=m.get("content", ""),
            tool_call_id=m.get("tool_call_id"),
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

    events = loop.recorder.events
    with open(timeline_path, "w", encoding="utf-8") as f_tl:
        for ev in events:
            f_tl.write(json.dumps({
                "event_id": ev.event_id,
                "ts": ev.ts,
                "event_type": ev.event_type.value,
                "payload": ev.payload,
                "prev_hash": ev.prev_hash,
                "hash": ev.compute_hash(),
            }, ensure_ascii=False) + "\n")

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
    from zall.core.model import ToolCall, Message
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
    out.write(f"  + tagged {target.name[:8]} with '{tag}'\n")


def _prune_sessions(days: int, out: Any) -> None:
    """Delete sessions older than the specified number of days.

    Keeps at least 5 most recent sessions regardless of age.
    Sessions older than _MAX_ABSOLUTE_AGE_DAYS are deleted even if <5 total.
    Only deletes complete session directories (with meta.json).
    """
    import time
    from datetime import datetime, timedelta

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
    from zall.eval.metrics import load_all_sessions, evaluate
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
    from zall.cli.replay import replay_session, compare_egress
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
        out.write(f"  ! AGENTS.md has changed since session was saved\n")
        out.write(f"  injected current project memory\n")
    else:
        note = Message(role="system",
                       content=f"[resumed from session {target.name[:8]}, user explicit]")
        msgs.append(note)

    state["resume_messages"] = msgs
    out.write(f"  + resumed {target.name[:8]} - {len(msgs)} messages loaded\n")
    out.write("  next input continues this context (type /clear to discard)\n")
    return "clear"
