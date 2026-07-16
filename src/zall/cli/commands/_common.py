"""zall.cli.commands._common — Shared infrastructure for slash commands.

Registry, decorators, routing, help/about rendering, and utility functions.
Extracted from _legacy.py (v0.2.1 refactor).

IPR constraints:
  IPR-3: only stdlib + rich + prompt_toolkit, no model SDK
"""

from __future__ import annotations

import difflib
from typing import Any, Callable

from zall.cli.render import _shared_console
from zall.skills import Skill, find_skill


# ──────────────────────────────────────────────────────────────────────────
# SlashCommand registry
# ──────────────────────────────────────────────────────────────────────────


class SlashCommand:
    """register表中的一条 slash command。"""

    __test__ = False

    def __init__(
        self,
        name: str,
        handler: Callable[[str, Any, Any, dict[str, Any]], str],
        *,
        aliases: tuple[str, ...] = (),
        description: str = "",
        category: str = "",
    ) -> None:
        self.name = name
        self.handler = handler
        self.aliases = aliases
        self.description = description
        self.category = category


# 全局commandregister表
_COMMANDS: dict[str, SlashCommand] = {}


def slash_command(
    name: str,
    *,
    aliases: tuple[str, ...] = (),
    description: str = "",
    category: str = "",
) -> Callable[..., Any]:
    """decorator: 将functionregister为 slash command。

    handler 签名: (arg: str, out: Any, loop: Any | None, state: dict) -> str
    返回值: "handled" | "exit" | "clear"
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        cmd = SlashCommand(
            name=name,
            handler=fn,
            aliases=aliases,
            description=description,
            category=category,
        )
        _COMMANDS[name] = cmd
        for alias in aliases:
            _COMMANDS[alias] = cmd
        _invalidate_command_meta_cache()  # O5: 注册后刷新缓存
        return fn

    return decorator


def get_known_commands() -> frozenset[str]:
    """return所有已知command名集合 (含别名)。"""
    return frozenset(_COMMANDS.keys())


# command元数据cache (避免每次补全都重建)
_COMMAND_META_CACHE: dict[str, str] | None = None


def get_command_meta() -> dict[str, str]:
    """return {command名: description} 字典 (供 prompt_toolkit completer 使用)。"""
    global _COMMAND_META_CACHE
    if _COMMAND_META_CACHE is not None:
        return _COMMAND_META_CACHE
    meta: dict[str, str] = {}
    for cmd_name, cmd in _COMMANDS.items():
        meta[cmd_name] = cmd.description
    _COMMAND_META_CACHE = meta
    return meta


def _invalidate_command_meta_cache() -> None:
    """commandregister后使cache失效 (下次 get_command_meta 重建)。"""
    global _COMMAND_META_CACHE
    _COMMAND_META_CACHE = None


# ──────────────────────────────────────────────────────────────────────────
# command分class常量
# ──────────────────────────────────────────────────────────────────────────

_CATEGORY_CONTEXT = "Context & Files"
_CATEGORY_SESSION = "Session"
_CATEGORY_MODEL = "Model & Config"
_CATEGORY_TOOLS = "Tools & Files"
_CATEGORY_NAV = "Navigation"
_CATEGORY_VIEW = "View"  # v0.4.0: 显示控制命令


# ──────────────────────────────────────────────────────────────────────────
# Navigation: about / help
# ──────────────────────────────────────────────────────────────────────────


def _print_about(out: Any) -> None:
    """打印项目design哲学。"""
    lines = [
        "  zall — a falsifiable, reproducible coding agent",
        "",
        "  Architecture-guaranteed, not prompt-engineering-guaranteed:",
        "    PR-0 (No Hallucination): stop_reason=STOP + no tool_calls → flagged",
        "    §4.3 (Context Cut):    no automatic cross-run history leak",
        "    §6.1 (Reproducibility): chain-hash timeline, replay without real model",
        "    §2   (R-Metric):       5-dimensional eval with anti-metric pairs",
        "    IPR-3 (Model-Agnostic): core/ never imports any model SDK",
        "",
        "  DESIGN.md → IMPL.md → code: full traceability",
        "  Type /help for commands, /eval to run evaluation.",
    ]
    if hasattr(out, "isatty") and out.isatty():
        c = _shared_console(out)
        for line in lines:
            if line.startswith("  zall"):
                c.print(f"[yellow]{line}[/]")
            elif line.startswith("    PR-0"):
                c.print("  [green]PR-0[/] (No Hallucination): [dim]stop_reason=STOP + no tool_calls → flagged[/]")
            elif line.startswith("    §4.3"):
                c.print("  [cyan]§4.3[/] (Context Cut):    [dim]no automatic cross-run history leak[/]")
            elif line.startswith("    §6.1"):
                c.print("  [cyan]§6.1[/] (Reproducibility): [dim]chain-hash timeline, replay without real model[/]")
            elif line.startswith("    §2"):
                c.print("  [cyan]§2[/]   (R-Metric):       [dim]5-dimensional eval with anti-metric pairs[/]")
            elif line.startswith("    IPR-3"):
                c.print("  [blue]IPR-3[/] (Model-Agnostic): [dim]core/ never imports any model SDK[/]")
            else:
                c.print(f"  [dim]{line.strip()}[/]")
    else:
        for line in lines:
            out.write(line + "\n")


def _print_help(out: Any, cmd_name: str = "") -> None:
    """渲染command帮助。"""
    _DETAILED_HELP: dict[str, str] = {
        "/help": (
            "  /help [command]\n"
            "    Show general help or detailed help for a specific command.\n"
            "    Examples:\n"
            "      /help           → list all commands\n"
            "      /help add       → show /add command details\n"
            "      /help --verbose → show all commands with examples"
        ),
        "/add": (
            "  /add <file> [file2 ...]\n"
            "    Add file(s) to agent context.\n"
            "    Limits: max 10 files, max 50KB per file.\n"
            "    Related: /drop to remove added files."
        ),
        "/drop": (
            "  /drop [file ...]\n"
            "    Remove previously added files from agent context.\n"
            "    Without arguments: lists added files.\n"
            "    /drop --all : removes all added files."
        ),
        "/fix": (
            "  /fix [command]\n"
            "    Auto-diagnose and fix the last command error."
        ),
        "/review": (
            "  /review [path]\n"
            "    Review uncommitted code changes (git diff)."
        ),
        "/retry": (
            "  /retry\n"
            "    Re-do the last agent step."
        ),
        "/search": (
            "  /search <query>\n"
            "    Search the web via DuckDuckGo."
        ),
        "/diff": (
            "  /diff [--stat | --full | --file <path>]\n"
            "    Show git working-tree diff with syntax highlighting."
        ),
        "/compact": (
            "  /compact\n"
            "    Compress conversation context to reduce token usage.\n"
            "    Timeline is preserved for reproducibility."
        ),
        "/undo": (
            "  /undo\n"
            "    Undo the last agent action (revert tool call).\n"
            "    Can undo multiple steps sequentially."
        ),
        "/checkpoint": (
            "  /checkpoint [save|list|restore|diff] [name]\n"
            "    Manage file system snapshots for safety.\n"
            "    save: create snapshot, list: show snapshots,\n"
            "    restore: rollback files, diff: show changes."
        ),
        "/revert": (
            "  /revert <index>\n"
            "    Restore files from a checkpoint by index."
        ),
        "/expand": (
            "  /expand [N|all]\n"
            "    Expand folded tool output in the display.\n"
            "    N = tool index (shown as → /expand N in the output).\n"
            "    'all' = expand all folded outputs.\n"
            "    Note: the AI already sees the full output; this is for human review."
        ),
        "/fold": (
            "  /fold\n"
            "    Fold/expand the last tool output (toggle)."
        ),
        "/remember": (
            "  /remember <text>\n"
            "    Save a reminder in AGENTS.md for future sessions.\n"
            "    The text is persisted across session resumes."
        ),
        "/forget": (
            "  /forget <keyword>\n"
            "    Remove matching entries from AGENTS.md project memory."
        ),
    }

    if cmd_name:
        full_name = cmd_name if cmd_name.startswith("/") else f"/{cmd_name}"
        detail = _DETAILED_HELP.get(full_name)
        if detail:
            console = _shared_console(out)
            console.print()
            console.print(f"[bold cyan]{full_name}[/]")
            console.print(detail)
            console.print()
        else:
            out.write(f"  no detailed help for {full_name} (try just /help)\n")
        return

    categories = [
        (_CATEGORY_CONTEXT, [
            ("/add <file> [...files]", "add file(s) to agent context"),
            ("/drop [file]", "remove added files from context"),
            ("/review [path]", "review uncommitted code changes"),
        ]),
        (_CATEGORY_SESSION, [
            ("/sessions", "list/search/tag/prune sessions"),
            ("/resume <id>", "resume a session's context into REPL"),
            ("/eval", "evaluate all sessions"),
            ("/replay <id>", "replay a session"),
            ("/cost", "show token usage + cost estimate"),
            ("/compact", "compress conversation context"),
            ("/undo", "revert last tool call"),
            ("/retry", "re-do last agent step"),
            ("/revert [id]", "restore files to a checkpoint"),
            ("/remember", "remember convention in AGENTS.md"),
            ("/forget", "reset AGENTS.md to template"),
        ]),
        (_CATEGORY_MODEL, [
            ("/model [n]", "show/switch model"),
            ("/max-steps [N]", "show or set step limit"),
            ("/verbose", "toggle verbose tool output"),
            ("/plan", "toggle plan mode (read-only)"),
            ("/doctor", "diagnose config / dependencies / directories"),
        ]),
        (_CATEGORY_TOOLS, [
            ("/diff", "show current git working-tree diff"),
            ("/web <url>", "fetch a web page"),
            ("/search <query>", "search the web (DuckDuckGo)"),
            ("/checkpoint", "list/save/restore file system snapshots"),
            ("/git", "git operations (status, commit, push, pull)"),
            ("/commit [msg]", "smart git commit"),
            ("/fix [cmd]", "auto-diagnose and fix last command error"),
            ("/skills", "list reusable workflows"),
            ("/skill <name>", "run a skill; args fill {input}"),
        ]),
        (_CATEGORY_NAV, [
            ("/help", "show this help"),
            ("/about", "project philosophy"),
            ("/version /v", "show version"),
            ("/clear", "clear screen"),
            ("/exit", "exit (or Ctrl-D)"),
        ]),
        (_CATEGORY_VIEW, [
            ("/expand [N|all]", "expand folded tool output"),
            ("/fold", "show fold status"),
        ]),
    ]

    if hasattr(out, "isatty") and out.isatty():
        from rich.table import Table

        console = _shared_console(out)
        console.print()
        console.print("[bold cyan]zall commands by category[/]")
        console.print()
        for cat_name, cmds in categories:
            table = Table(title=cat_name, show_header=False,
                          border_style="dim", padding=(0, 1),
                          box=None, collapse_padding=True)
            table.add_column("command", style="cyan", no_wrap=True, width=24)
            table.add_column("description", style="dim")
            for cmd, desc in cmds:
                table.add_row(cmd, desc)
            console.print(table)
            console.print()
        console.print("  [dim]type anything to chat + use tools; context is shared[/]")
        console.print("  [dim]zall 'task' one-shot = independent run + session + judge[/]")
        console.print("  [dim]CLI flags: --verbose, --version/-V, --yes/-y, --json, --model, --max-steps, --no-stream[/]")
        console.print("  [dim]try '/help <command>' for detailed usage of a specific command[/]")
    else:
        out.write("  zall commands:\n")
        for cat_name, cmds in categories:
            out.write(f"\n  [{cat_name}]\n")
            for cmd, desc in cmds:
                out.write(f"    {cmd:24s} {desc}\n")
        out.write("\n  type anything to chat + use tools; context is shared\n")
        out.write("  zall 'task' one-shot = independent run + session + judge\n")
        out.write("  CLI flags: --verbose, --version/-V, --yes/-y, --json, --model, --max-steps, --no-stream\n")


# ──────────────────────────────────────────────────────────────────────────
# Skills routing
# ──────────────────────────────────────────────────────────────────────────


def _print_skills(skills: list[Skill], out: Any) -> None:
    """列出所有register的 skill。"""
    if not skills:
        out.write("  (no skills registered)\n")
        return
    out.write(f"  skills ({len(skills)}):\n")
    for sk in skills:
        out.write(f"    {sk.name:20s} {sk.description or ''}\n")
    out.write("  use '/skill <name> [args]' to invoke\n")


def _route_skill(cmd: str, skills: list[Skill], out: Any) -> tuple[str, str]:
    """parse /skill command。return ('handled', '') 或 ('task', expanded_text)。"""
    parts = cmd.split()
    head = parts[0].lower()
    if head not in ("/skill", "/skills"):
        return ("none", "")
    if head == "/skills" or len(parts) < 2:
        _print_skills(skills, out)
        return ("handled", "")
    skill_name = parts[1]
    arg = parts[2].strip() if len(parts) > 2 else ""
    skill = find_skill(skills, skill_name)
    if skill is None:
        out.write(f"  unknown skill: {skill_name} (try /skills to list)\n")
        return ("handled", "")
    expanded = skill.expand(arg)
    if not expanded:
        out.write(f"  skill '{skill.name}' expanded to empty task\n")
        return ("handled", "")
    out.write(f"  › skill: {skill.name}\n")
    out.flush()
    return ("task", expanded)


# ──────────────────────────────────────────────────────────────────────────
# Command routing
# ──────────────────────────────────────────────────────────────────────────


def _handle_bare_slash(out: Any) -> None:
    """孤立的 / → 显示command快速参考。"""
    out.write("  commands:\n")
    for name in sorted(_COMMANDS.keys()):
        cmd = _COMMANDS[name]
        if cmd.name == name:
            out.write(f"    /{name:12s} {cmd.description}\n")


def _suggest_command(name: str) -> str | None:
    """did-you-mean: 对未知commandreturn最接近的已知command。"""
    target = name.lstrip("/").lower()
    candidates = [c.lstrip("/").lower() for c in _COMMANDS.keys()]
    matches = difflib.get_close_matches(target, candidates, n=1, cutoff=0.6)
    return matches[0] if matches else None


def _guess_common_command(name: str, out: Any) -> None:
    """对常见error拼写给出针对性prompt。"""
    hints = {
        "quit": "use /exit or /q to quit",
        "ver": "use /version or /v to show version",
        "config": "use /doctor to diagnose config",
        "history": "use /sessions to list history",
        "ls": "use /sessions to list sessions",
        "skills": "use /skills to list reusable workflows",
        "model": "usage: /model <name> or /model to pick interactively",
        "init": "usage: /init to initialize project config",
        "undo": "usage: /undo to revert last tool action",
    }
    if name in hints:
        out.write(f"  hint: {hints[name]}\n")


def handle_slash(cmd: str, state: dict[str, Any], out: Any, loop: Any = None) -> str:
    """handle slash command。return 'handled' | 'exit' | 'clear' | 'none'。"""
    c = cmd.strip()
    if not c.startswith("/"):
        return "none"
    if c == "/" or c == "/ ":
        _handle_bare_slash(out)
        return "handled"
    parts = c.split(None, 1)
    name = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    cmd_entry = _COMMANDS.get(name)
    if cmd_entry is not None:
        return cmd_entry.handler(arg, out, loop, state)

    suggestion = _suggest_command(name)
    if suggestion:
        out.write(f"  unknown: {name} — did you mean /{suggestion}? (try /help)\n")
    else:
        out.write(f"  unknown: {name} (try /help)\n")
        _guess_common_command(name.lstrip("/").lower(), out)
    return "handled"


# ──────────────────────────────────────────────────────────────────────────
# Tab completion
# ──────────────────────────────────────────────────────────────────────────


def _setup_completion(skills: list[Any]) -> None:
    """register tab 补全 (slash command + /skill <name>)。"""
    try:
        import prompt_toolkit  # noqa: F401
        return  # prompt_toolkit 提供 completer, 无需 readline
    except ImportError:
        pass
    try:
        import readline
    except ImportError:
        try:
            import pyreadline3 as readline  # type: ignore[import-not-found,no-redef]
        except ImportError:
            try:
                import pyreadline as readline  # type: ignore[import-not-found,no-redef]
            except ImportError:
                return
    candidates: list[str] = list(get_known_commands())
    for sk in skills:
        candidates.append(f"/skill {sk.name}")
    seen: set[str] = set()
    uniq = [c for c in candidates if not (c in seen or seen.add(c))]  # type: ignore[func-returns-value]

    def _complete(text: str, state: int) -> str | None:
        matches = [c for c in uniq if c.startswith(text)]
        return matches[state] if state < len(matches) else None

    readline.set_completer(_complete)  # type: ignore[attr-defined]
    readline.set_completer_delims("")  # type: ignore[attr-defined]
    readline.parse_and_bind("tab: complete")  # type: ignore[attr-defined]


# ──────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────


def _cmd_init_simple(out: Any) -> None:
    """创建 .zall inittemplatefile (4 个) + TrustAnchor init化。
    v0.4.0: AGENTS.md 使用 _generate_agents_md 自动填充项目信息。
    """
    from pathlib import Path
    zall_dir = Path.cwd() / ".zall"
    zall_dir.mkdir(parents=True, exist_ok=True)
    # v0.4.0: dynamic生成 AGENTS.md (检测项目type自动填充)
    agents_md_content = _generate_agents_md(str(Path.cwd()))
    files: dict[str, str] = {
        "rules.toml": _INIT_RULES_TOML,
        "AGENTS.md": agents_md_content,
        "mcp.toml": _INIT_MCP_TOML,
        "skills.toml": _INIT_SKILLS_TOML,
    }
    for name, content in files.items():
        target = zall_dir / name
        if target.exists():
            out.write(f"  · {name} already exists\n")
        else:
            target.write_text(content, encoding="utf-8")
            out.write(f"  ✓ {name} created\n")

    # O5: TrustAnchor init化 (生成 ed25519 key + anchor指纹)
    try:
        from zall.core.verifiability import FileTrustAnchor
        try:
            anchor = FileTrustAnchor()
            if anchor.anchor_id:
                out.write(f"  ✓ trust anchor initialized (id={anchor.anchor_id})\n")
        finally:
            # FileTrustAnchor 在 ~/.zall/ init化, 不dependency cwd
            pass
    except Exception as e:
        out.write(f"  · trust anchor setup skipped: {e}\n")

    out.write(f"  project initialized in {zall_dir}\n")


def _estimate_tokens(text: str) -> int:
    """CJK 感知的 token 估算: ASCII ~4 字符/token, CJK ~2 字符/token。"""
    if not text:
        return 0
    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f')
    ascii_count = len(text) - cjk_count
    return max(1, (ascii_count // 4) + (cjk_count // 2))


def _recalc_usage_from_timeline(recorder: Any, state: dict[str, Any] | None) -> None:
    """从 timeline 重新statistics token usage (供 /undo 后校正).

    B2 fix: 从 MODEL_CALL 事件的 usage 载荷提取真实 token 计数,
    替代旧版只扫 content 估算 completion 却赋值给 prompt 的 bug。
    """
    try:
        events = list(recorder.events)
        total_prompt = 0
        total_completion = 0
        for ev in events:
            if ev.event_type == EventType.MODEL_CALL:
                payload = ev.payload
                usage = payload.get("usage")
                if usage and isinstance(usage, dict):
                    total_prompt += int(usage.get("prompt", 0) or 0)
                    total_completion += int(usage.get("completion", 0) or 0)
                else:
                    content = str(payload.get("content", "") or "")
                    total_completion += _estimate_tokens(content)
        if state is not None:
            state["usage"] = {
                "prompt": max(0, total_prompt),
                "completion": max(0, total_completion),
            }
    except Exception:
        if state is not None:
            state["usage"] = {"prompt": 0, "completion": 0}


def _check_network_basic(api_base: str) -> tuple[str, str]:
    """DNS-level networkcheck。return (state, 详情)。"""
    import socket
    from urllib.parse import urlparse
    if not api_base:
        return ("error", "no api_base configured")
    host = urlparse(api_base).hostname
    if not host:
        return ("error", f"cannot parse host from {api_base}")
    try:
        socket.getaddrinfo(host, 80, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        return ("error", f"DNS resolution failed for {host}: {e}")
    return ("ok", f"DNS resolution ok for {host}")


def _check_network_http(api_base: str) -> tuple[str, str]:
    """TCP/HTTP 连通性check — 不只是 DNS, 还要validate服务端可达。

    使用 socket 做 TCP 连接测试 (不依赖 httpx, 避免额外依赖)。
    返回 (状态, 详情)。
    """
    import socket
    from urllib.parse import urlparse
    host = ""
    port = 0
    if not api_base:
        return ("error", "no api_base configured")
    try:
        parsed = urlparse(api_base)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            return ("error", f"cannot parse host from {api_base}")
        # TCP 连接test (timeout 5 秒)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        try:
            result = sock.connect_ex((host, port))
            if result == 0:
                return ("ok", f"TCP connection ok to {host}:{port}")
            else:
                return ("error", f"TCP connection failed to {host}:{port} (err={result})")
        finally:
            sock.close()
    except socket.gaierror as e:
        return ("error", f"DNS resolution failed for {host}: {e}")
    except Exception as e:
        return ("error", f"connection check failed: {e}")


def _check_git_health() -> tuple[str, str]:
    """Git 仓库健康check。

    返回 (状态, 详情)。
    """
    import subprocess
    try:
        r = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return ("error", "git not found on PATH")
        git_version = r.stdout.strip() or "git available"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ("error", "git not found on PATH")

    try:
        r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return ("warn", f"{git_version} — not a git repository")
    except (subprocess.TimeoutExpired, OSError):
        return ("warn", f"{git_version} — cannot check git status")

    # Git 仓库, checkstate
    try:
        r = subprocess.run(["git", "status", "--porcelain"],
                           capture_output=True, text=True, timeout=5)
        dirty = bool(r.stdout.strip())
        branch_r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                  capture_output=True, text=True, timeout=5)
        branch = branch_r.stdout.strip() if branch_r.returncode == 0 else "?"
        if dirty:
            changes = len([line for line in r.stdout.split("\n") if line.strip()])
            return ("warn", f"{git_version} — branch {branch}, {changes} uncommitted change(s)")
        return ("ok", f"{git_version} — branch {branch}, clean")
    except (subprocess.TimeoutExpired, OSError):
        return ("ok", f"{git_version}")


def _check_mcp_health(project_dir: str | None = None) -> tuple[str, str]:
    """MCP server config健康check。

    检查 .zall/mcp.toml 中的 server 配置, 验证命令是否在 PATH 中。
    返回 (状态, 详情)。
    """
    import shutil
    from pathlib import Path
    from zall.mcp.config import load_mcp_config

    project_path = project_dir or str(Path.cwd())
    try:
        servers = load_mcp_config(project_path=str(project_path))
    except Exception:
        return ("warn", "cannot load MCP config")

    if not servers:
        return ("ok", "no MCP servers configured (optional)")

    issues: list[str] = []
    for s in servers:
        cmd = s.command.split()[0] if " " in s.command else s.command
        if shutil.which(cmd) is None:
            issues.append(f"{s.name}: command '{cmd}' not found on PATH")
    if issues:
        return ("warn", "; ".join(issues))
    return ("ok", f"{len(servers)} MCP server(s) configured, commands found")


def _check_dependency_version(dep_name: str) -> str:
    """checkdependency包version, return可读version字符串或 'MISSING'。"""
    import importlib
    try:
        mod = importlib.import_module(dep_name)
        ver = getattr(mod, "__version__", None) or getattr(mod, "version", None) or ""
        return ver if ver else "ok"
    except ImportError:
        return "MISSING"


def _check_path_tools() -> tuple[str, str]:
    """check常用tool是否在 PATH 中。

    返回 (状态, 详情)。
    """
    import shutil
    tools = ["git", "python", "pip", "node", "npm"]
    found = []
    missing = []
    for t in tools:
        if shutil.which(t):
            found.append(t)
        else:
            missing.append(t)
    if missing:
        return ("warn", f"found: {', '.join(found)}; missing: {', '.join(missing)}")
    return ("ok", f"all: {', '.join(found)}")


def _check_anchor_dir(label: str, key: Path, log: Path, init: Path) -> str:
    """Check a single trust anchor directory (user or project level).

    Returns a formatted status string like "user: key=32B, log=5 entries" or "user: no key".
    """
    ok = key.exists()
    if not ok:
        return f"{label}: no key"
    key_size = key.stat().st_size
    log_entries = 0
    if log.exists():
        try:
            log_entries = len([line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()])
        except Exception:
            pass
    init_ok = init.exists()
    result = f"{label}: key={key_size}B, log={log_entries} entries"
    if not init_ok:
        result += f" ({label} init: MISSING)"
    return result


def _check_trust_anchor(project_dir: str | None = None) -> tuple[str, str]:
    """TrustAnchor statecheck。

    检查 ~/.zall/ 和 .zall/ 下的 TrustAnchor 文件。
    返回 (状态, 详情)。
    """
    from pathlib import Path

    # 用户级 (~/.zall/)
    home_dir = Path.home()
    home_key = home_dir / ".zall" / "trust_anchor_key"
    home_log = home_dir / ".zall" / "trust_anchor.log"
    home_init = home_dir / ".zall" / "trust_anchor_init.txt"

    # 项目级 (.zall/)
    root = Path(project_dir) if project_dir else Path.cwd()
    proj_key = root / ".zall" / "trust_anchor_key"
    proj_log = root / ".zall" / "trust_anchor.log"
    proj_init = root / ".zall" / "trust_anchor_init.txt"

    parts: list[str] = []

    # check用户级
    user_status = _check_anchor_dir("user", home_key, home_log, home_init)
    parts.append(user_status)

    # check项目级
    proj_status = _check_anchor_dir("project", proj_key, proj_log, proj_init)
    parts.append(proj_status)

    user_ok = home_key.exists()
    proj_ok = proj_key.exists()
    if not user_ok and not proj_ok:
        return ("warn", "no trust anchor (optional — run 'zall init' to create); " + "; ".join(parts))
    return ("ok", "; ".join(parts))


def _check_disk_space(path: str | None = None) -> tuple[str, str]:
    """checkdisk空间 (sessions directory)。

    返回 (状态, 详情)。
    """
    import shutil
    from pathlib import Path
    check_path = path or str(Path.home() / ".zall" / "sessions")
    p = Path(check_path)
    if not p.exists():
        return ("ok", "no sessions yet")
    try:
        usage = shutil.disk_usage(p.anchor if p.anchor else p)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        if free_gb < 1.0:
            return ("warn", f"low disk space: {free_gb:.1f}GB free / {total_gb:.1f}GB total")
        return ("ok", f"{free_gb:.1f}GB free / {total_gb:.1f}GB total")
    except Exception:
        return ("ok", "cannot check disk space")


def _auto_step_loop(loop: Any, out: Any, max_steps: int = 5) -> None:
    """自动execute loop.step() 若干次, 用于 /fix 等command。"""
    for _ in range(max_steps):
        try:
            result = loop.step()
        except Exception as e:
            out.write(f"  ⚠ step error: {e}\n")
            break
        if result.is_terminal:
            out.write("  (session ended)\n")
            break
        if result.kind == "awaiting_input":
            break


# ──────────────────────────────────────────────────────────────────────
# Init template constants
# ──────────────────────────────────────────────────────────────────────

_INIT_RULES_TOML = """\
# zall project rules (\u00a74.2.1 context_judge)
[allow]
tools = []
[deny]
tools = []
commands = ["rm -rf", "git push --force", "git reset --hard"]
[settings]
judge = "none"
"""

_INIT_AGENTS_MD = """\
# AGENTS.md \u2014 zall project memory (\u00a79.4)
## Project Overview
(Describe your project, tech stack, conventions)
## Conventions
- Code style:
- Test conventions:
- Prohibited operations:
## Common Commands
- Test:
- Build:
- Run:
"""

_INIT_MCP_TOML = """\
# zall MCP server registration (\u00a79.2.11)
# [[servers]]
# name = "filesystem"
# command = "mcp-server-filesystem"
# args = ["/abs/path/to/root"]
"""

_INIT_SKILLS_TOML = '''\
# zall skills (\u00a79.2.7)
[[skills]]
name = "review"
description = "review current git diff for bugs and regressions"
prompt = "Review the current git working-tree diff. Focus on correctness bugs, regressions, and missing tests."

[[skills]]
name = "explain"
description = "explain a file's purpose and structure"
prompt = "Read {input} and explain its purpose, structure, and key functions."
'''


# ──────────────────────────────────────────────────────────────────────
# View commands: /expand, /fold (v0.4.0)
# ──────────────────────────────────────────────────────────────────────


@slash_command("/expand", description="expand folded tool output", category=_CATEGORY_VIEW)
def cmd_expand(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """展开折叠的tooloutput。

    Usage:
        /expand      — 展开序号列表 (需指定序号)
        /expand N    — 展开第 N 个折叠的工具
        /expand all  — 展开所有折叠的工具
    """
    renderer = state.get("_renderer") if state else None
    if renderer is None or not hasattr(renderer, "expand_tool"):
        out.write("  (no renderer or no folded tools)\n")
        return "handled"
    arg = arg.strip()
    if arg == "all":
        count = renderer.expand_all_tools()
        out.write(f"  expanded {count} tool(s)\n")
    elif arg.isdigit():
        idx = int(arg)
        if renderer.expand_tool(idx):
            out.write(f"  expanded tool #{idx}\n")
        else:
            out.write(f"  tool #{idx} not found or already expanded\n")
    else:
        folded = state.get("_renderer").folded_count if state and hasattr(state.get("_renderer", None), "folded_count") else 0  # type: ignore[union-attr]
        if folded > 0:
            out.write(f"  {folded} tool(s) folded. Use /expand <N> or /expand all\n")
        else:
            out.write("  no folded tools\n")
    return "handled"


@slash_command("/fold", description="re-fold tool output (show compact)", category=_CATEGORY_VIEW)
def cmd_fold(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """折叠已展开的tooloutput (当前为 no-op, 仅prompt用 /expand toggle)。

    Usage:
        /fold — 提示当前折叠状态
    """
    renderer = state.get("_renderer") if state else None
    folded = renderer.folded_count if renderer and hasattr(renderer, "folded_count") else 0
    if folded > 0:
        out.write(f"  {folded} tool(s) folded. Use /expand <N> to show more details.\n")
    else:
        out.write("  all tools already in compact view. Use /verbose to switch to full output.\n")
    return "handled"


# ──────────────────────────────────────────────────────────────────────
# Generate AGENTS.md dynamically (v0.4.0)
# ──────────────────────────────────────────────────────────────────────


def _generate_agents_md(cwd: str) -> str:
    """根据项目config自动生成 AGENTS.md content。

    检测项目类型 (pyproject.toml, package.json 等), 提取项目名称、
    技术栈、测试命令和构建命令, 填充到 AGENTS.md 模板中。
    """
    from pathlib import Path

    root = Path(cwd)

    # default值
    project_name = "(your project name)"
    tech_stack = []
    test_cmd = ""
    build_cmd = ""
    run_cmd = ""
    conventions = []

    # 检测 pyproject.toml (Python)
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        project_name = "(Python project)"
        tech_stack.append("Python")
        try:
            text = pyproject.read_text(encoding="utf-8")
            for line in text.split("\n"):
                s = line.strip()
                if s.startswith("name = "):
                    project_name = s.split("=", 1)[1].strip().strip('"\'')
                if "pytest" in s:
                    test_cmd = "pytest"
                if "ruff" in s:
                    conventions.append("Use ruff for linting")
                if "mypy" in s:
                    conventions.append("Use mypy for type checking")
        except Exception:
            pass
        if not test_cmd:
            test_cmd = "pytest"
        if not build_cmd:
            build_cmd = "pip install -e ."
        run_cmd = "python -m"

    # 检测 package.json (Node.js)
    package_json = root / "package.json"
    if package_json.exists():
        project_name = "(Node.js project)"
        tech_stack.append("Node.js")
        try:
            import json as _json
            data = _json.loads(package_json.read_text(encoding="utf-8"))
            if data.get("name"):
                project_name = data["name"]
            scripts = data.get("scripts", {})
            if scripts.get("test"):
                test_cmd = f"npm test  # {scripts['test']}"
            if scripts.get("build"):
                build_cmd = f"npm run build  # {scripts['build']}"
            if scripts.get("start"):
                run_cmd = f"npm start  # {scripts['start']}"
        except Exception:
            pass
        if not test_cmd:
            test_cmd = "npm test"
        if not build_cmd:
            build_cmd = "npm run build"

    # 检测 Cargo.toml (Rust)
    cargo = root / "Cargo.toml"
    if cargo.exists():
        project_name = "(Rust project)"
        tech_stack.append("Rust")
        if not test_cmd:
            test_cmd = "cargo test"
        if not build_cmd:
            build_cmd = "cargo build"
        if not run_cmd:
            run_cmd = "cargo run"

    # 检测 go.mod (Go)
    go_mod = root / "go.mod"
    if go_mod.exists():
        project_name = "(Go project)"
        tech_stack.append("Go")
        if not test_cmd:
            test_cmd = "go test ./..."
        if not build_cmd:
            build_cmd = "go build"
        if not run_cmd:
            run_cmd = "go run ."

    tech_line = ", ".join(tech_stack) if tech_stack else "(describe your tech stack)"
    conv_lines = "\n".join(f"- {c}" for c in conventions) if conventions else "- Code style:\n- Test conventions:\n- Prohibited operations:"

    return f"""# AGENTS.md — zall project memory (§9.4)
## Project Overview
- Project: {project_name}
- Tech: {tech_line}
## Conventions
{conv_lines}
## Common Commands
- Test: {test_cmd or "(describe)"}
- Build: {build_cmd or "(describe)"}
- Run: {run_cmd or "(describe)"}
"""




# Lazy import to avoid circular dependency
from zall.core.verifiability import EventType  # noqa: E402, F401