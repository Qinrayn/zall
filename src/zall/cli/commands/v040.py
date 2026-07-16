"""zall.cli.commands.v040 — v0.4.0 新系统 CLI 命令。

Commands: /lsp, /sandbox, /plugin, /codegraph, /chatstate

IPR constraints:
  IPR-3: only stdlib + rich, no model SDK
"""

from __future__ import annotations

import sys
from typing import Any

from zall.cli.commands._common import (
    _CATEGORY_TOOLS, _CATEGORY_SESSION, slash_command,
)


# ═══════════════════════════════════════════════════════════════════
# /lsp — LSP 诊断信息
# ═══════════════════════════════════════════════════════════════════


@slash_command(
    name="lsp",
    category=_CATEGORY_TOOLS,
    description="Show LSP diagnostics or start language server",
)
def cmd_lsp(args: str, out: Any, err: Any, state: dict[str, Any]) -> int:
    """显示 LSP 诊断信息或启动语言服务器。

    Usage:
        /lsp           — 显示所有诊断
        /lsp <file>    — 显示指定文件的诊断
        /lsp <file> error — 只显示错误
    """
    loop = state.get("loop")
    if loop is None:
        _print_err(err, "No active session. Start a session first.")
        return 1

    lsp = getattr(loop, "_lsp_manager", None)
    if lsp is None:
        _print_err(err, "LSP manager not initialized.")
        _print_hint(err, "Set up LSP with: /lsp start <language>")
        return 1

    parts = args.strip().split()
    file_path = parts[0] if len(parts) >= 1 else ""
    severity = parts[1] if len(parts) >= 2 else "all"

    try:
        if file_path == "start" and len(parts) >= 2:
            # Start a language server
            lang = parts[1]
            try:
                lsp.start_server(lang)
                _print_ok(out, f"Started LSP server for {lang}")
                return 0
            except KeyError:
                _print_err(err, f"Unknown language: {lang}")
                return 1
            except RuntimeError as e:
                _print_err(err, str(e))
                return 1

        if file_path == "stop":
            lsp.shutdown_all()
            _print_ok(out, "Stopped all LSP servers")
            return 0

        if file_path == "status":
            summary = lsp.summary()
            _print_ok(out, (
                f"LSP Servers: {summary['active_servers']}\n"
                f"Files tracked: {summary['open_files']}\n"
                f"Diagnostics: {summary['diagnostics_errors']} errors, "
                f"{summary['diagnostics_warnings']} warnings"
            ))
            return 0

        # Show diagnostics
        if file_path:
            lsp.open_file(file_path)
            all_diags = lsp.all_diagnostics
            # Find matching file
            for fpath, diags in all_diags.items():
                if file_path in fpath:
                    display_diags(out, {fpath: diags}, severity)
                    return 0
            _print_ok(out, f"No diagnostics for {file_path}")
            return 0

        # All diagnostics
        display_diags(out, lsp.all_diagnostics, severity)
        return 0

    except Exception as e:
        _print_err(err, f"LSP command failed: {e}")
        return 1


def display_diags(out: Any, all_diags: dict[str, list[Any]], severity: str) -> None:
    """显示诊断信息。"""
    total_errors = 0
    total_warnings = 0
    lines: list[str] = ["[LSP Diagnostics]"]

    for fpath in sorted(all_diags.keys()):
        diags = all_diags[fpath]
        file_errors = [d for d in diags if getattr(d, "severity_label", "") == "error"]
        file_warnings = [d for d in diags if getattr(d, "severity_label", "") == "warning"]
        filtered = diags

        if severity == "error":
            filtered = file_errors
        elif severity == "warning":
            filtered = file_errors + file_warnings

        if not filtered:
            continue

        total_errors += len(file_errors)
        total_warnings += len(file_warnings)
        lines.append(f"\n  {fpath}")
        lines.append(f"    {len(file_errors)} errors, {len(file_warnings)} warnings")

        for d in filtered:
            label = getattr(d, "severity_label", "?")
            msg = getattr(d, "message", "?")
            line = getattr(d, "line", 0) + 1
            col = getattr(d, "column", 0) + 1
            lines.append(f"    {label}:{line}:{col}: {msg[:120]}")

    if not lines:
        _print_ok(out, "[No diagnostics — project looks clean]")
        return

    summary = f"\nTotal: {total_errors} errors, {total_warnings} warnings"
    _print_ok(out, summary + "".join(lines))


# ═══════════════════════════════════════════════════════════════════
# /sandbox — 沙箱模式控制
# ═══════════════════════════════════════════════════════════════════


@slash_command(
    name="sandbox",
    category=_CATEGORY_TOOLS,
    description="Control sandbox isolation mode",
)
def cmd_sandbox(args: str, out: Any, err: Any, state: dict[str, Any]) -> int:
    """控制沙箱隔离模式。

    Usage:
        /sandbox           — 显示当前沙箱状态
        /sandbox none      — 无隔离 (默认)
        /sandbox process   — 子进程隔离
        /sandbox status    — 显示沙箱状态
        /sandbox apply     — 应用沙箱修改回主项目
        /sandbox diff      — 显示沙箱中的修改差异
    """
    from zall.sandbox import Sandbox, SandboxMode

    loop = state.get("loop")
    sandbox = state.get("sandbox")

    args = args.strip().lower()

    if not args or args == "status":
        if sandbox is None:
            _print_ok(out, "Sandbox: not active (none mode)")
            return 0
        mode = getattr(sandbox, "mode", "?")
        path = sandbox.get_path()
        status = f"Sandbox mode: {mode}"
        if path:
            status += f"\n  Workspace: {path}"
        _print_ok(out, status)
        return 0

    if args == "none":
        state["sandbox"] = Sandbox(mode=SandboxMode.NONE)
        _print_ok(out, "Sandbox: none mode (no isolation)")
        return 0

    if args == "process":
        state["sandbox"] = Sandbox(
            mode=SandboxMode.PROCESS,
            project_dir=state.get("cwd", "."),
        )
        _print_ok(out, "Sandbox: process mode (isolated subprocess)")
        return 0

    if args == "apply":
        if sandbox is None:
            _print_err(err, "No active sandbox")
            return 1
        if sandbox.apply_changes():
            _print_ok(out, "Changes applied to main project")
        else:
            _print_err(err, "Failed to apply changes")
        return 0

    if args == "diff":
        if sandbox is None:
            _print_err(err, "No active sandbox")
            return 1
        diff = sandbox.get_diff()
        if diff:
            _print_ok(out, diff)
        else:
            _print_ok(out, "[No changes in sandbox]")
        return 0

    _print_err(err, f"Unknown sandbox command: {args}")
    _print_hint(err, "Usage: /sandbox [none|process|status|apply|diff]")
    return 1


# ═══════════════════════════════════════════════════════════════════
# /codegraph — 代码图索引控制
# ═══════════════════════════════════════════════════════════════════


@slash_command(
    name="codegraph",
    category=_CATEGORY_TOOLS,
    description="Manage codebase index for symbol search",
)
def cmd_codegraph(args: str, out: Any, err: Any, state: dict[str, Any]) -> int:
    """管理代码图索引。

    Usage:
        /codegraph           — 显示索引状态
        /codegraph index     — 构建/刷新索引
        /codegraph search <q> — 搜索符号
        /codegraph outline <f> — 显示文件大纲
    """
    loop = state.get("loop")
    cg = state.get("codegraph")

    if cg is None:
        _print_err(err, "CodeGraph not initialized.")
        _print_hint(err, "Initialize with: /codegraph index")
        return 1

    args = args.strip()

    if not args or args == "status":
        stats = cg.get_stats()
        _print_ok(out, (
            f"[CodeGraph Status]\n"
            f"  Status: {stats.get('status', 'unknown')}\n"
            f"  Files: {stats.get('file_count', 0)}\n"
            f"  Symbols: {stats.get('symbol_count', 0)}\n"
            f"  Errors: {stats.get('error_count', 0)}"
        ))
        return 0

    if args == "index":
        import time
        start = time.time()
        _print_ok(out, "Indexing codebase...")
        cg.build_index()
        elapsed = time.time() - start
        stats = cg.get_stats()
        _print_ok(out, (
            f"Indexed {stats.get('file_count', 0)} files, "
            f"{stats.get('symbol_count', 0)} symbols "
            f"in {elapsed:.1f}s"
        ))
        return 0

    if args.startswith("search "):
        query = args[7:]
        results = cg.search(query)
        if not results:
            _print_ok(out, f"No symbols found matching '{query}'")
            return 0
        lines = [f"Symbols matching '{query}' ({len(results)}):"]
        for sym in results:
            loc = getattr(sym, "location", None)
            fn = getattr(loc, "file_path", "?") if loc else "?"
            ln = getattr(loc, "line", 0) if loc else 0
            kind = getattr(sym, "kind", "")
            kind_label = kind.value if hasattr(kind, "value") else str(kind)
            lines.append(f"  {kind_label} {sym.name} @ {fn}:{ln}")
        _print_ok(out, "\n".join(lines))
        return 0

    if args.startswith("outline "):
        file_path = args[8:]
        outline = cg.get_outline(file_path)
        if not outline:
            _print_ok(out, f"No symbols in {file_path}")
            return 0
        lines = [f"Outline of {file_path}:"]
        for entry in outline:
            name = entry.get("name", "?")
            kind = entry.get("kind", "?")
            line = entry.get("line", 0)
            lines.append(f"\n  {kind} {name} @ {line}")
            for child in entry.get("children", []):
                c_name = child.get("name", "?")
                c_kind = child.get("kind", "?")
                c_line = child.get("line", 0)
                lines.append(f"    {c_kind} {c_name} @ {c_line}")
        _print_ok(out, "\n".join(lines))
        return 0

    _print_err(err, f"Unknown codegraph command: {args}")
    return 1


# ═══════════════════════════════════════════════════════════════════
# /chatstate — ChatState 诊断
# ═══════════════════════════════════════════════════════════════════


@slash_command(
    name="chatstate",
    category=_CATEGORY_SESSION,
    description="Show ChatState diagnostics",
)
def cmd_chatstate(args: str, out: Any, err: Any, state: dict[str, Any]) -> int:
    """显示 ChatState 诊断信息。"""
    loop = state.get("loop")
    if loop is None:
        _print_err(err, "No active session")
        return 1

    cs = getattr(loop, "chat_state", None)
    if cs is None:
        _print_ok(out, "ChatState: not active (using legacy message list)")
        return 0

    lines = ["[ChatState Diagnostics]"]
    lines.append(f"  Messages: {cs.message_count}")
    lines.append(f"  Events: {len(cs.events)}")
    lines.append(f"  Token usage: {cs.usage.total_tokens}")
    lines.append(f"  Model calls: {cs.usage.call_count}")
    lines.append(f"  Compactions: {cs.compaction_count}")
    lines.append(f"  Prompt index: {cs.prompt_index}")
    _print_ok(out, "\n".join(lines))
    return 0


# ═══════════════════════════════════════════════════════════════════
# /plugin — 插件管理
# ═══════════════════════════════════════════════════════════════════


@slash_command(
    name="plugin",
    category=_CATEGORY_TOOLS,
    description="Manage plugins",
)
def cmd_plugin(args: str, out: Any, err: Any, state: dict[str, Any]) -> int:
    """管理插件。

    Usage:
        /plugin           — 列出已发现和已加载的插件
        /plugin list      — 列出所有插件
        /plugin load <n>  — 加载指定插件
        /plugin install <url> — 从 Git 安装插件
    """
    system = state.get("plugin_system")
    if system is None:
        from zall.plugin import PluginSystem
        system = PluginSystem()
        state["plugin_system"] = system

    args = args.strip().lower()

    if not args or args == "list":
        discovered = system.discover()
        if not discovered:
            _print_ok(out, "[No plugins found]")
            return 0

        lines = ["[Discovered plugins:]"]
        for p in discovered:
            status = "loaded" if system.get_plugin(p.name) else "discovered"
            lines.append(f"  {p.name} ({p.scope.value}) — {status}")
        _print_ok(out, "\n".join(lines))
        return 0

    if args.startswith("load "):
        name = args[5:]
        loaded = system.load_plugin(name)
        if loaded is None:
            _print_err(err, f"Plugin '{name}' not found")
            return 1
        _print_ok(out, f"Loaded plugin: {name}")
        return 0

    if args.startswith("install "):
        url = args[8:]
        _print_ok(out, f"Installing plugin from {url}...")
        loaded = system.install_from_git(url)
        if loaded is None:
            _print_err(err, "Installation failed. Check the URL and try again.")
            return 1
        _print_ok(out, f"Installed and loaded plugin: {loaded.name}")
        return 0

    _print_err(err, f"Unknown plugin command: {args}")
    return 1


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _print_ok(out: Any, msg: str) -> None:
    """Print success message."""
    print(msg, file=out or sys.stdout)


def _print_err(err: Any, msg: str) -> None:
    """Print error message."""
    print(f"  ✗ {msg}", file=err or sys.stderr)


def _print_hint(err: Any, msg: str) -> None:
    """Print hint message."""
    print(f"  hint: {msg}", file=err or sys.stderr)