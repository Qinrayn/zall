"""zall.cli.commands.files — File/context/tool commands.

Extracted from _legacy.py (v0.2.1 refactor).
Commands: /add, /drop, /diff, /web, /search

IPR constraints:
  IPR-3: only stdlib + rich, no model SDK
"""

from __future__ import annotations

import glob as glob_module
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from zall.cli.commands._common import _CATEGORY_CONTEXT, _CATEGORY_TOOLS, slash_command
from zall.cli.render import _shared_console
from zall.tools.search import SearchTool
from zall.tools.web_fetch import WebFetchTool

# Extracted from _legacy.py lines 381-521
# ──────────────────────────────────────────────────────────────────────
# Context & Files commands
# ──────────────────────────────────────────────────────────────────────


@slash_command("/add", description="add file(s) to agent context", category=_CATEGORY_CONTEXT)
def cmd_add(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if not arg:
        out.write("  usage: /add <file> [file2 ...]\n")
        out.write("  examples:\n")
        out.write("    /add src/main.py          → inject one file\n")
        out.write("    /add src/*.py             → glob pattern\n")
        out.write("    /add src/main.py tests/   → multiple paths\n")
        return "handled"

    if loop is None:
        out.write("  start a conversation first (type a message) then /add files\n")
        return "handled"

    raw_paths = arg.split()
    file_paths: list[str] = []
    for rp in raw_paths:
        expanded = glob_module.glob(rp, recursive=True)
        if expanded:
            file_paths.extend(expanded)
        else:
            file_paths.append(rp)
    file_paths = list(dict.fromkeys(file_paths))

    if not file_paths:
        out.write(f"  no files matched: {arg}\n")
        return "handled"

    MAX_ADD_FILES = 10
    if len(file_paths) > MAX_ADD_FILES:
        out.write(f"  too many files ({len(file_paths)}), max {MAX_ADD_FILES}\n")
        file_paths = file_paths[:MAX_ADD_FILES]

    MAX_FILE_SIZE = 50 * 1024
    added_count = 0
    if state is not None:
        added_tracker = state.setdefault("_added_files", set())
    else:
        added_tracker = None

    for fp in file_paths:
        p = Path(fp)
        if not p.exists():
            out.write(f"  \u2717 not found: {fp}\n")
            continue
        if not p.is_file():
            out.write(f"  \u2717 not a file: {fp}\n")
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            out.write(f"  \u2717 cannot read {fp}: {e}\n")
            continue
        if len(content) > MAX_FILE_SIZE:
            out.write(f"  \u2717 {fp} too large ({len(content)} bytes, max {MAX_FILE_SIZE})\n")
            continue

        abs_path = str(p.absolute())
        injected = (
            f"[user added file: {abs_path}]\n"
            f"```\n{content}\n```"
        )
        loop.add_user_file_message(injected)
        added_count += 1

        if added_tracker is not None:
            added_tracker.add(abs_path)
        if state is not None:
            artifact_list = state.setdefault("_artifact_files", [])
            if abs_path not in artifact_list:
                artifact_list.append(abs_path)
        out.write(f"  \u2713 added: {fp}\n")

    if added_count > 0:
        out.write(f"  \u2192 {added_count} file(s) injected into context\n")
    return "handled"


@slash_command("/drop", description="remove added files from context", category=_CATEGORY_CONTEXT)
def cmd_drop(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if loop is None:
        out.write("  start a conversation first (type a message) then /drop files\n")
        return "handled"
    if state is None:
        out.write("  (no /add tracking in current state)\n")
        return "handled"

    artifact_files = state.get("_artifact_files", [])
    if not artifact_files:
        out.write("  (no files added via /add)\n")
        return "handled"

    if not arg:
        out.write(f"  added files ({len(artifact_files)}):\n")
        for af in artifact_files:
            out.write(f"    {af}\n")
        out.write("  use /drop <file> to remove, or /drop --all to remove all\n")
        return "handled"

    if arg == "--all":
        removed_count = loop.remove_messages_by_predicate(
            lambda m: m.role == "user" and "[user added file:" in m.content
        )
        state["_artifact_files"] = []
        if state.get("_added_files") is not None:
            state["_added_files"] = set()
        out.write(f"  \u2713 removed {removed_count} file injection(s) from context\n")
        return "handled"

    targets = arg.split()
    removed_count = 0
    for target in targets:
        target_abs = str(Path(target).absolute())
        if target in artifact_files:
            artifact_files.remove(target)
            removed_count += 1
        elif target_abs in artifact_files:
            artifact_files.remove(target_abs)
            removed_count += 1

    if removed_count == 0:
        out.write(f"  (no matching files found: {arg})\n")
        return "handled"

    remaining = set(artifact_files)
    loop.remove_messages_by_predicate(
        lambda m: (
            m.role == "user"
            and "[user added file:" in m.content
            and not any(f"[user added file: {af}]" in m.content for af in remaining)
        )
    )
    out.write(f"  \u2713 removed {removed_count} file(s) from context\n")
    return "handled"




# Extracted from _legacy.py lines 1097-1162
@slash_command("/search", description="search the web", category=_CATEGORY_TOOLS)
def cmd_search(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if not arg:
        out.write("  usage: /search <query>\n")
        out.write("  example: /search python argparse tutorial\n")
        return "handled"

    out.write(f"  searching: {arg}\n")
    tool = SearchTool()
    result = tool.execute({"query": arg, "max_results": 5})

    if not result.success:
        out.write(f"  \u2717 search failed: {result.output}\n")
        return "handled"

    artifacts = result.artifacts or {}
    results_list = artifacts.get("results", [])
    results_count = artifacts.get("results_count", 0)

    if hasattr(out, "isatty") and out.isatty():
        c = _shared_console(out)
        c.print(f"  [cyan]Search:[/] {arg}")
        c.print(f"  [dim]{results_count} results[/]")
        c.print()
        for i, r in enumerate(results_list, 1):
            title = r.get("title", "(no title)")
            snippet = r.get("snippet", "")
            url = r.get("url", "")
            c.print(f"  [bold]{i}. {title}[/]")
            if snippet:
                c.print(f"     {snippet}")
            c.print(f"     [dim]{url}[/]")
            c.print()
    else:
        out.write(f"  Search results for: {arg}\n")
        out.write(f"  ({results_count} results)\n")
        for i, r in enumerate(results_list, 1):
            out.write(f"  {i}. {r.get('title', '')}\n")
            out.write(f"     {r.get('url', '')}\n")
            out.write(f"     {r.get('snippet', '')}\n")
    return "handled"


@slash_command("/web", description="fetch a web page", category=_CATEGORY_TOOLS)
def cmd_web(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if not arg:
        out.write("  usage: /web <url>\n")
        return "handled"
    tool = WebFetchTool()
    result = tool.execute({"url": arg, "max_chars": 5000})
    if not result.success:
        out.write(f"  {result.output}\n")
        return "handled"
    title = (result.artifacts or {}).get("title", "")
    chars = (result.artifacts or {}).get("chars", 0)
    if hasattr(out, "isatty") and out.isatty():
        c = _shared_console(out)
        if title:
            c.print(f"  [cyan]Title:[/] {title}")
        c.print(f"  [dim]{chars} chars[/]")
        c.print(Panel(result.output[:2000], border_style="dim", padding=(0, 1), expand=False))
    else:
        out.write(f"  Web: {arg}\n")
        out.write(result.output[:2000] + "\n")
    return "handled"



# Extracted from _legacy.py lines 1169-1247
@slash_command("/diff", description="show git diff", category=_CATEGORY_TOOLS)
def cmd_diff(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    try:
        s = subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                           text=True, timeout=10, encoding="utf-8", errors="replace")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        out.write("  (git unavailable)\n")
        return "handled"
    if s.returncode != 0:
        out.write("  (not a git repository)\n")
        return "handled"
    if not s.stdout.strip():
        out.write("  (no uncommitted changes)\n")
        return "handled"

    try:
        d = subprocess.run(["git", "diff", "--stat", "HEAD"], capture_output=True,
                           text=True, timeout=10, encoding="utf-8", errors="replace")
        if d.stdout.strip():
            out.write(d.stdout)
        else:
            out.write("  (no diff)\n")
            return "handled"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        out.write("  (git diff failed)\n")
        return "handled"

    added = 0
    deleted = 0
    for line in d.stdout.split("\n"):
        m = re.search(r"(\d+)\s+insertion", line)
        if m:
            added += int(m.group(1))
        m = re.search(r"(\d+)\s+deletion", line)
        if m:
            deleted += int(m.group(1))

    if added or deleted:
        if hasattr(out, "isatty") and out.isatty():
            c = _shared_console(out)
            c.print(f"  [green]+{added}[/] [red]-{deleted}[/]")
        else:
            out.write(f"  +{added}/-{deleted} lines\n")

    try:
        files_out = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        diff_files = [f for f in files_out.stdout.split("\n") if f.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        diff_files = []

    if diff_files and hasattr(out, "isatty") and out.isatty():
        try:
            c = _shared_console(out)
            MAX_DISPLAY_FILES = 5
            for fname in diff_files[:MAX_DISPLAY_FILES]:
                try:
                    fd = subprocess.run(
                        ["git", "diff", "HEAD", "--", fname],
                        capture_output=True, text=True, timeout=10,
                    )
                    if fd.stdout.strip():
                        diff_text = fd.stdout
                        lines = diff_text.split("\n")
                        if len(lines) > 80:
                            diff_text = "\n".join(lines[:80])
                            diff_text += f"\n... (truncated, {len(lines)} total)"
                        syntax = Syntax(diff_text, "diff", theme="ansi_dark", line_numbers=False)
                        c.print(Panel(syntax, border_style="dim", title=f"[cyan]{fname}[/]"))
                except Exception:
                    pass
            if len(diff_files) > MAX_DISPLAY_FILES:
                c.print(f"  [dim]... and {len(diff_files) - MAX_DISPLAY_FILES} more file(s)[/]")
        except ImportError:
            pass
    return "handled"

