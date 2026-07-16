"""System prompt builder + environment metadata.

Design:
  - Builds the system prompt: base rules + runtime env + project memory + repo map
  - CwdMeta: current working directory, git branch, git remote
  - Project memory: .zall/AGENTS.md read-only injection
  - Repo map: auto-generated project structure summary

IPR constraints:
  IPR-0: observer failures must not change RunEgress
  IPR-3: stdlib only, no model SDK
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from zall.core.context import Context
from zall.mcp.tool import MCPTool

import os as _os

# ──────────────────────────────────────────────────────────────────────────
# CwdMeta
# ──────────────────────────────────────────────────────────────────────────


class CwdMeta:
    """Current working directory metadata (read-only)."""

    __test__ = False

    def __init__(self) -> None:
        self.cwd_path = str(Path.cwd())
        self.git_branch, self.git_remote = self._get_git_meta()

    @staticmethod
    def _get_git_meta() -> tuple[str | None, str | None]:
        """P4: 并行执行两个 git 子process, 减少 ~100-400ms 启动时间。

        O9: 使用 ThreadPoolExecutor 并行执行 git rev-parse 和 git config,
        替代原来的顺序执行 (总耗时=两命令之和)。
        """
        branch: str | None = None
        remote: str | None = None

        def _get_branch() -> str | None:
            try:
                r = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True, text=True, timeout=3,
                )
                return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                return None

        def _get_remote() -> str | None:
            try:
                r = subprocess.run(
                    ["git", "config", "--get", "remote.origin.url"],
                    capture_output=True, text=True, timeout=3,
                )
                return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                return None

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_branch = executor.submit(_get_branch)
            fut_remote = executor.submit(_get_remote)
            branch = fut_branch.result()
            remote = fut_remote.result()

        return branch, remote


def get_cached_cwd_meta(state: dict[str, Any]) -> CwdMeta:
    """Cache CwdMeta to avoid spawning git subprocess on every prompt."""
    meta = state.get("_cached_cwd_meta")
    if meta is None:
        meta = CwdMeta()
        state["_cached_cwd_meta"] = meta
    return meta


# ──────────────────────────────────────────────────────────────────────────
# Runtime tool detection
# ──────────────────────────────────────────────────────────────────────────

_RUNTIME_TOOLS_CACHE: list[str] | None = None


def clear_runtime_tools_cache() -> None:
    """Clear the runtime tools detection cache (for test isolation)."""
    global _RUNTIME_TOOLS_CACHE
    _RUNTIME_TOOLS_CACHE = None


def _validate_runtime_cmd(cmd: str) -> bool:
    """Verify a command is actually runnable (not just present in PATH).

    shutil.which only checks file existence + executable bit, not whether
    the command can actually run. On Windows, the py launcher may point
    to an uninstalled Python version.
    """
    try:
        r = subprocess.run(
            [cmd, "--version"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _detect_runtime_tools() -> list[str]:
    """Detect actual paths for runtime tools (python/py/git/node), inject into system prompt.

    Uses shutil.which to find candidates, then validates each by actually
    running <cmd> --version. Falls back to next candidate if unusable.

    P5: 用 ThreadPoolExecutor 并行执行 --version 检测, 减少 ~200-800ms 启动时间。

    Cached at process level (tool paths don't change within a process lifetime).
    """
    global _RUNTIME_TOOLS_CACHE
    if _RUNTIME_TOOLS_CACHE is not None:
        return _RUNTIME_TOOLS_CACHE

    if sys.platform == "win32":
        candidates: list[tuple[str, list[str]]] = [
            ("python", ["py", "python", "python3"]),
            ("git", ["git"]),
            ("node", ["node"]),
            ("npm", ["npm"]),
        ]
    else:
        candidates = [
            ("python", ["python3", "python"]),
            ("git", ["git"]),
            ("node", ["node"]),
            ("npm", ["npm"]),
        ]

    # P5: 收集所有候选command, parallelvalidate
    from concurrent.futures import ThreadPoolExecutor, as_completed
    all_tasks: list[tuple[str, str]] = []
    for label, cmds in candidates:
        for cmd in cmds:
            path = shutil.which(cmd)
            if path:
                all_tasks.append((label, cmd))

    valid_cmds: set[str] = set()
    # O9: 任务数 ≤ 3 时同步执行, 避免 ThreadPoolExecutor 的 ~30ms 启动开销
    if len(all_tasks) > 3:
        with ThreadPoolExecutor(max_workers=min(len(all_tasks), 8)) as executor:
            fut_map = {executor.submit(_validate_runtime_cmd, cmd): (label, cmd)
                       for label, cmd in all_tasks}
            for fut in as_completed(fut_map):
                label, cmd = fut_map[fut]
                if fut.result():
                    valid_cmds.add(cmd)
    else:
        for label, cmd in all_tasks:
            if _validate_runtime_cmd(cmd):
                valid_cmds.add(cmd)

    # P5: 为每个 label 选第一个可用command (sequentialpreserve)
    used_cmds: set[str] = set()
    lines: list[str] = []
    for label, cmds in candidates:
        found = False
        for cmd in cmds:
            if cmd in valid_cmds and cmd not in used_cmds:
                path = shutil.which(cmd)
                if cmd != label:
                    lines.append(f"  {label}: use `{cmd}` (full path: {path})")
                else:
                    lines.append(f"  {label}: {path}")
                used_cmds.add(cmd)
                found = True
                break
        if not found:
            lines.append(f"  {label}: not found in PATH")

    _RUNTIME_TOOLS_CACHE = lines
    return lines


# ──────────────────────────────────────────────────────────────────────────
# System prompt
# ──────────────────────────────────────────────────────────────────────────


_SYSTEM_PROMPT_BASE = """\
You are an autonomous coding agent running inside zall. You operate through tools.

CORE DIRECTIVE (most important):
You ACT through tools. For ANY request that needs information about files, the filesystem,
the environment, or running something, you MUST emit a tool_call FIRST to gather facts —
never answer from assumption. For ANY actionable request (create / modify / delete / run),
you MUST emit a tool_call to actually DO it — never reply with text that only describes
what you intend to do (eg. "I will create a folder" without a bash tool_call is a failure).
Execute the tool call in this very turn. Never return an empty response.

TASK DECOMPOSITION (autonomy):
For complex or multi-step tasks, break the work down:
  1. First, explore: use list_dir/grep/glob to understand the project structure.
     A repo map of the project is provided in ENVIRONMENT to give you an overview.
  2. Then, plan: use todo_list to lay out sub-tasks and track progress.
  3. Delegate and parallelize: use spawn_subagent for ISOLATED sub-tasks
     (it spawns a sub-agent that can read files and run commands independently).
     When a task has multiple INDEPENDENT parts, spawn them in parallel and
     combine results — this is faster than sequential work.
     The sub-agent is read-only by default — it CANNOT write files.
  4. Execute incrementally: make small, focused changes. After each change,
     verify it works (run tests, check syntax) before proceeding.
     Prefer edit_file over write_file for small changes (preserves context).
  5. Verify: run tests or check results after each logical change.
     Do not defer all verification to the end.

INCREMENTAL WORK (best practice):
  - Small changes, verified one at a time, produce better results than large rewrites.
  - After editing a file, verify it: check syntax (python -c "compile(...)" / npm run build).
  - If a test suite exists, run relevant tests after each change.
  - Use bash with focused commands (not broad "run all tests" until the end).

When you see a task that has multiple independent parts, prefer to use
spawn_subagent for each part in parallel and then combine the results.

RULES (binding):
1. To read/write files or run commands, you MUST emit a tool_call. Never write tool
   output inside your text content — that is a hallucination (PR-0 violation).
2. When you are done, stop with a natural-language summary. Do not fabricate results.
3. If you cannot complete the task, say so honestly. "undecidable" is acceptable;
   faking "done" is not.
4. Read files before editing them. Make targeted edits, not full rewrites.
5. Prefer the most specific tool (grep/glob/list_dir) over bash when possible.
6. Use batch_edit when making the same type of change across multiple files
   (e.g. renaming a function, updating imports, changing a pattern).
7. Be concise. Do not restate the user's question or over-explain. Answer directly.
8. If unsure where things are, explore first: list_dir/glob to find files, then
   read_file/grep to inspect. Do not guess paths — use tools to discover them.

TOOLS available: read_file, write_file, edit_file, batch_edit, bash, grep, glob, list_dir, web_fetch, spawn_subagent, todo_list.
  - read_file(path)           read a file's content
  - write_file(path, content) create or fully overwrite a file
  - edit_file(path, old, new) targeted string replacement (read first!)
  - batch_edit(edits)         batch edit multiple files in one call (all-or-nothing)
  - bash(command)             run a shell command (use sparingly; prefer grep/read_file)
  - grep(pattern, path?)      search file contents (uses ripgrep if available)
  - glob(pattern, path?)      find files by name pattern
  - list_dir(path)            list directory entries
  - web_fetch(url)            fetch a web page and extract text content
  - spawn_subagent(prompt)    delegate an isolated sub-task to a sub-agent (read-only by default)
  - todo_list(todos)          update the task progress checklist (display only; does not decide completion)
"""


# AGENTS.md cache (invalidated by mtime)
_AGENTS_MD_CACHE: dict[str, tuple[float, str | None]] = {}


def read_agents_md(cwd: str) -> str | None:
    """Read project memory from .zall/AGENTS.md, silently return None on failure.

    This is a read-only projection: injecting project memory into the system prompt,
    not agent self-modification. Cached by file mtime to avoid disk reads.
    """
    try:
        p = Path(cwd) / ".zall" / "AGENTS.md"
        if not p.is_file():
            _AGENTS_MD_CACHE[cwd] = (0, None)
            return None
        mtime = p.stat().st_mtime
        cached = _AGENTS_MD_CACHE.get(cwd)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        content = p.read_text(encoding="utf-8").strip() or None
        _AGENTS_MD_CACHE[cwd] = (mtime, content)
        return content
    except (OSError, UnicodeDecodeError):
        _AGENTS_MD_CACHE[cwd] = (0, None)
        return None


# Repo map cache (invalidated by mtime)
_REPO_MAP_CACHE: dict[str, tuple[float, str]] = {}


def build_repo_map(cwd: str, max_depth: int = 3) -> str:
    """Build a project structure summary.

    Generates a directory tree + top-level symbols (function/class signatures)
    for each source file. Injected into the system prompt to help the model
    understand the codebase, reducing exploratory tool calls.

    Limits: max 50 files, 5000 characters, cached by mtime.
    """
    _SKIP_DIRS = frozenset({
        ".git", ".zall", ".zcode", "venv", ".venv", "node_modules",
        "__pycache__", ".tox", "dist", "build", ".egg-info",
        ".pytest_cache", "target", ".github", "idea",
    })
    _EXTENSIONS = frozenset({
        ".py", ".js", ".ts", ".jsx", ".tsx", ".rs", ".go", ".java",
        ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".swift", ".kt",
        ".toml", ".yaml", ".yml", ".json", ".md", ".css", ".html",
    })
    try:
        p = Path(cwd)
        if not p.is_dir():
            return ""
        mtime = p.stat().st_mtime
        cached = _REPO_MAP_CACHE.get(cwd)
        if cached is not None and cached[0] == mtime:
            return cached[1]

        lines: list[str] = []
        lines.append("PROJECT STRUCTURE (repo map, auto-generated):")
        file_count = 0
        for root, dirs, files in os_walk(str(p), topdown=True):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS
                       and not (d.startswith(".") and d != ".zall")]
            depth = root[len(str(p)):].count(os_sep)
            if depth > max_depth:
                dirs.clear()
                continue
            indent = "  " * depth
            rel_dir = root[len(str(p)) + 1:] if root != str(p) else ""
            if rel_dir:
                lines.append(f"{indent}{rel_dir}/")
            for fn in sorted(files):
                ext = Path(fn).suffix.lower()
                if ext not in _EXTENSIONS:
                    continue
                file_count += 1
                if file_count > 50:
                    break
                symbols = _extract_symbols(Path(root) / fn, ext)
                if symbols:
                    syms_joined = ", ".join(symbols[:5])
                    lines.append(f"{indent}  {fn}  ({syms_joined})")
                else:
                    lines.append(f"{indent}  {fn}")
            if file_count > 50:
                break

        result = "\n".join(lines)
        if len(result) > 5000:
            result = result[:5000] + "\n... (truncated)"
        _REPO_MAP_CACHE[cwd] = (mtime, result)
        return result
    except (OSError, PermissionError):
        return ""


# Pre-compiled symbol extraction regexes
_RE_PY_SYMBOLS = re.compile(r"^(?:async\s+)?(?:def|class)\s+(\w+)", re.MULTILINE)
_RE_JS_SYMBOLS = re.compile(
    r"^(?:export\s+)?(?:function|class|const|let|var|interface|type|enum)\s+(\w+)",
    re.MULTILINE,
)
_RE_GO_SYMBOLS = re.compile(r"^(?:func|type|struct|interface)\s+(\w+)", re.MULTILINE)
_RE_RS_SYMBOLS = re.compile(
    r"^(?:pub\s+)?(?:fn|struct|enum|trait|impl|mod|type|const)\s+(\w+)", re.MULTILINE,
)
_RE_JAVA_SYMBOLS = re.compile(
    r"^(?:public|private|protected)?\s*(?:abstract|final|static|open|data|sealed|inner)?"
    r"\s*(?:class|interface|enum|@interface|record|fun|object)\s+(\w+)",
    re.MULTILINE,
)
_RE_C_SYMBOLS = re.compile(
    r"^(?:static\s+)?(?:int|void|char|long|float|double|size_t|unsigned|struct|enum)"
    r"(?:\s+\*?)?\s+(\w+)\s*\(",
    re.MULTILINE,
)
_RE_CPP_FN_SYMBOLS = re.compile(
    r"^(?:virtual\s+|static\s+|inline\s+)?"
    r"(?:int|void|char|long|float|double|size_t|unsigned|bool|auto|class|struct)"
    r"(?:\s+\*?&?)?\s+(\w+)\s*(?:\<[^>]*\>)?\s*\(",
    re.MULTILINE,
)
_RE_CPP_TYPE_SYMBOLS = re.compile(r"^(?:class|struct|enum|namespace)\s+(\w+)", re.MULTILINE)
_RE_SWIFT_SYMBOLS = re.compile(
    r"^(?:public|private|internal|open|fileprivate)?\s*(?:static|class|override)?"
    r"\s*(?:func|class|struct|enum|protocol|extension|actor)\s+(\w+)",
    re.MULTILINE,
)
_RE_RB_SYMBOLS = re.compile(
    r"^(?:def|class|module)\s+(?:\w+(?:::\w+)*)", re.MULTILINE,
)
_RE_PHP_SYMBOLS = re.compile(
    r"^(?:public|private|protected|static|abstract|final)?\s*"
    r"(?:function|class|interface|trait|enum)\s+(\w+)",
    re.MULTILINE,
)


def _extract_symbols(filepath: Path, ext: str) -> list[str]:
    """Extract top-level symbols (function/class definitions) from a source file."""
    symbols: list[str] = []
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")[:3000]
        if ext == ".py":
            for m in _RE_PY_SYMBOLS.finditer(text):
                symbols.append(m.group(1))
        elif ext in (".js", ".ts", ".jsx", ".tsx"):
            for m in _RE_JS_SYMBOLS.finditer(text):
                symbols.append(m.group(1))
        elif ext == ".go":
            for m in _RE_GO_SYMBOLS.finditer(text):
                symbols.append(m.group(1))
        elif ext == ".rs":
            for m in _RE_RS_SYMBOLS.finditer(text):
                symbols.append(m.group(1))
        elif ext in (".java", ".kt"):
            for m in _RE_JAVA_SYMBOLS.finditer(text):
                symbols.append(m.group(1))
        elif ext in (".c", ".h"):
            for m in _RE_C_SYMBOLS.finditer(text):
                symbols.append(m.group(1))
        elif ext in (".cpp", ".hpp", ".cc", ".cxx"):
            for m in _RE_CPP_FN_SYMBOLS.finditer(text):
                symbols.append(m.group(1))
            for m in _RE_CPP_TYPE_SYMBOLS.finditer(text):
                symbols.append(m.group(1))
        elif ext == ".swift":
            for m in _RE_SWIFT_SYMBOLS.finditer(text):
                symbols.append(m.group(1))
        elif ext == ".rb":
            for m in _RE_RB_SYMBOLS.finditer(text):
                symbols.append(m.group(0))
        elif ext == ".php":
            for m in _RE_PHP_SYMBOLS.finditer(text):
                symbols.append(m.group(1))
    except OSError:
        pass
    return symbols[:10]


os_walk = _os.walk
os_sep = _os.sep


class PromptBuilder:
    """Prompt 构建器 — 每个 section 一个 method, 支持链式调用。

    Usage:
        body = (PromptBuilder(context)
                .add_env()
                .add_plan_mode(plan_mode)
                .add_project_memory()
                .add_repo_map(enable=True)
                .add_mcp_tools(mcp_tools)
                .add_skills(skills)
                .add_session_memory()
                .build())
    """

    def __init__(self, context: Context) -> None:
        self._context = context
        self._parts: list[str] = []

    def add_env(self) -> PromptBuilder:
        """Environment info section."""
        cwd = self._context.cwd_meta
        env_lines = ["", "ENVIRONMENT (you are running here):"]
        env_lines.append("  agent: zall - model-agnostic coding agent")
        env_lines.append(f"  cwd: {cwd.cwd_path}")
        if cwd.git_branch:
            env_lines.append(f"  git branch: {cwd.git_branch}")
        if cwd.git_remote:
            env_lines.append(f"  git remote: {cwd.git_remote}")
        env_lines.append(f"  platform: {sys.platform}")
        env_lines.extend(_detect_runtime_tools())
        if sys.platform == "win32":
            env_lines.append(
                "  NOTE: You are on Windows. The bash tool executes via PowerShell, so you can "
                "use bash-compatible syntax: `mkdir -p a/b/c`, single-quoted strings, `&&`, `|`, "
                "`echo`, `cat`, `ls` (as aliases). PowerShell cmdlets also work. "
                "Use the python/python-path shown above to run scripts."
            )
        env_lines.append(
            "  You share context across turns in this REPL. Use tools to inspect "
            "the environment; do not guess paths."
        )
        self._parts.append(_SYSTEM_PROMPT_BASE + "\n".join(env_lines))
        return self

    def add_plan_mode(self, enabled: bool) -> PromptBuilder:
        """Plan mode section (analysis-first, read-only)."""
        if enabled:
            self._parts.append(
                "\n\nPLAN MODE (analysis-first, read-only):\n"
                "  You are in PLAN mode. Your goal is to ANALYZE and DESIGN, not to execute.\n"
                "  Follow this structured thinking process:\n"
                "    1. UNDERSTAND: Explore the codebase to understand the current state.\n"
                "    2. ANALYZE: Identify what needs to change and why.\n"
                "    3. DESIGN: Propose a concrete plan with specific file changes.\n"
                "  RULES for PLAN mode:\n"
                "    - Use read-only tools (read_file, list_dir, grep, glob) freely.\n"
                "    - Do NOT create, modify, or delete files - that's for execution mode.\n"
                "    - Do NOT run destructive bash commands (no write operations).\n"
                "    - Output your analysis clearly: structure it as Understanding -> "
                "Analysis -> Proposed Changes.\n"
                "    - Be specific: mention exact file paths, line numbers, and code "
                "patterns you would change.\n"
                "  The user will review your plan before authorizing execution."
            )
        return self

    def add_project_memory(self) -> PromptBuilder:
        """Project memory from .zall/AGENTS.md."""
        agents_md = read_agents_md(self._context.cwd_meta.cwd_path)
        if agents_md:
            self._parts.append(
                "\n\nPROJECT MEMORY (from .zall/AGENTS.md - project conventions, read-only):\n"
                + agents_md
            )
        return self

    def add_repo_map(self, enable: bool = True) -> PromptBuilder:
        """Auto-generated repo map."""
        if enable:
            repo_map = build_repo_map(self._context.cwd_meta.cwd_path)
            if repo_map:
                self._parts.append("\n\n" + repo_map)
        return self

    def add_mcp_tools(self, mcp_tools: tuple[MCPTool, ...] = ()) -> PromptBuilder:
        """MCP tools listing."""
        if mcp_tools:
            by_server: dict[str, list[str]] = {}
            for t in mcp_tools:
                by_server.setdefault(t.server_name, []).append(t.tool_id)
            mcp_lines = [
                "",
                "MCP tools (registered from connected MCP servers; require confirmation "
                "by default - they are NOT auto-approved):",
            ]
            for srv, ids in by_server.items():
                mcp_lines.append(f"  - {srv}: {', '.join(ids)}")
            self._parts.append("\n" + "\n".join(mcp_lines))
        return self

    def add_skills(self, skills: list[Any] | None = None) -> PromptBuilder:
        """Skills listing (progressive disclosure)."""
        if skills:
            skill_lines = ["", "Available skills (use /skill <name> to run one):"]
            for s in skills:
                desc = getattr(s, "description", "") or ""
                skill_lines.append(f"  - {s.name}: {desc}")
            self._parts.append("\n" + "\n".join(skill_lines))
        return self

    def add_session_memory(self) -> PromptBuilder:
        """Cross-session memory injection."""
        try:
            from zall.core.memory import get_session_memory
            memory_ctx = get_session_memory().build_context()
            if memory_ctx:
                self._parts.append("\n" + memory_ctx)
        except Exception:
            pass  # Memory loading failure does not block
        return self

    def build(self) -> str:
        return "".join(self._parts)


# O9: build_system_prompt 缓存 — 参数不变时复用上次结果
_SYSTEM_PROMPT_CACHE: dict[str, tuple[str, str]] = {}
"""key=(cwd, plan_mode, enable_repo_map, mcp_tool_ids, skill_names) → (built_prompt, _)"""


def _prompt_cache_key(
    context: Context, mcp_tools: tuple[MCPTool, ...], plan_mode: bool,
    enable_repo_map: bool, skills: list[Any] | None,
) -> str:
    """构造缓存 key。"""
    parts = [
        context.cwd_meta.cwd_path if hasattr(context, 'cwd_meta') else '',
        str(plan_mode),
        str(enable_repo_map),
        str(mcp_tools),
        str([s.name for s in skills]) if skills else '',
    ]
    return "|".join(parts)


def build_system_prompt(
    context: Context, mcp_tools: tuple[MCPTool, ...] = (),
    *, enable_repo_map: bool = True, plan_mode: bool = False,
    skills: list[Any] | None = None,
) -> str:
    """Build the system prompt: base rules + runtime env + project memory + repo map.

    Lets the model know it's running in zall, where it is, git status,
    project conventions, avoiding guesswork. Both run() and REPL use this
    function for zero-duplicate project memory injection.

    mcp_tools: registered MCP tools, appended to the tool list.
    enable_repo_map: whether to inject repo map (default True).
    plan_mode: whether to inject analysis-first plan mode prompt.
    skills: available skills for progressive disclosure (name + description only).

    O9: 缓存结果 (参数不变时复用), 避免 REPL 每轮都重建 system prompt。
    """
    global _SYSTEM_PROMPT_CACHE
    cache_key = _prompt_cache_key(context, mcp_tools, plan_mode, enable_repo_map, skills)
    cached = _SYSTEM_PROMPT_CACHE.get(cache_key)
    if cached is not None:
        return cached[0]

    result = (PromptBuilder(context)
              .add_env()
              .add_plan_mode(plan_mode)
              .add_project_memory()
              .add_repo_map(enable=enable_repo_map)
              .add_mcp_tools(mcp_tools)
              .add_skills(skills)
              .add_session_memory()
              .build())

    # O9: 缓存, 上限 8 条防内存泄漏
    _SYSTEM_PROMPT_CACHE[cache_key] = (result, "")
    if len(_SYSTEM_PROMPT_CACHE) > 8:
        _SYSTEM_PROMPT_CACHE.clear()
    return result


def clear_system_prompt_cache() -> None:
    """清除系统 prompt 缓存 (供测试隔离)。"""
    global _SYSTEM_PROMPT_CACHE
    _SYSTEM_PROMPT_CACHE.clear()
