"""zall.tools.bash — Execute shell command (ACI design).

ACI Design notes:
  - always uses timeout (default 120s, prevents hang)
  - working directory persists (no per-step cd)
  - returns stdout + stderr + exit_code
  - output truncation (prevents large output from polluting context)
  - self-protection (prevents agent from terminating itself)

IPR constraints:
  IPR-0: invariant tests at tests/test_bash_invariants.py
  IPR-1: corresponds to DESIGN.md section 4.2
  IPR-3: only stdlib, no model SDK
"""

from __future__ import annotations

import locale
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import base64
from typing import Any, Protocol, runtime_checkable

from zall.core.tool import ToolResult
from zall.core.tool_kind import ToolKind

MAX_OUTPUT_BYTES = 50_000  # Maximum output (50KB, prevents context pollution)


# ── Platform shell detection (cross-platform fix) ──

def _detect_shell() -> str:
    """Detect the best available shell for the current platform.

    Returns a shell command string suitable for subprocess calls.

    Windows:
      1. Git Bash (C:\\Program Files\\Git\\bin\\bash.exe) — preferred
      2. PowerShell (powershell.exe) — fallback
    macOS/Linux:
      /bin/bash, or /bin/sh as fallback
    """
    if sys.platform == "win32":
        # Try Git Bash first (provides native bash experience)
        git_bash_paths = [
            "C:\\Program Files\\Git\\bin\\bash.exe",
            "C:\\Program Files (x86)\\Git\\bin\\bash.exe",
        ]
        for path in git_bash_paths:
            if os.path.isfile(path):
                return path
        # Also try via shutil.which (in case Git is in PATH)
        which_bash = shutil.which("bash")
        if which_bash:
            return which_bash
        # Fallback to PowerShell
        return "powershell"
    else:
        # macOS / Linux: use /bin/bash, fallback to /bin/sh
        for candidate in ("/bin/bash", "/bin/sh"):
            if os.path.isfile(candidate):
                return candidate
        which_bash = shutil.which("bash") or shutil.which("sh")
        if which_bash:
            return which_bash
        return "/bin/sh"  # last resort


def _is_powershell(shell_cmd: str) -> bool:
    """Check if the detected shell is PowerShell (not Git Bash)."""
    return "powershell" in shell_cmd.lower()


# ── BashExecutor Protocol (v0.3.0: extractable strategy) ──


@runtime_checkable
class BashExecutor(Protocol):
    """Strategy protocol for bash command execution.

    Implementations:
      - PopenExecutor: default subprocess-based (current behavior)
      - PtyExecutor: PTY-based for interactive commands (optional)

    The BashTool uses the default executor but allows injection
    of PtyExecutor for enhanced capabilities.
    """

    def execute(
        self,
        command: str,
        timeout: int,
        cwd: str | None = None,
    ) -> ToolResult:
        """Execute a shell command and return the result.

        Args:
            command: The command to execute
            timeout: Timeout in seconds
            cwd: Working directory (None = current)

        Returns:
            ToolResult with stdout/stderr/exit_code
        """
        ...


def _preferred_encoding() -> str:
    """Get the system's preferred encoding (Windows Chinese is GBK/CP936, do not hardcode UTF-8).

    Root cause of garbled text: subprocess output encoding is determined by the OS locale,
    not UTF-8. Hardcoding encoding="utf-8" on Chinese Windows misreads GBK bytes as UTF-8,
    producing garbled output.
    """
    try:
        enc = locale.getpreferredencoding(False)
        if enc:
            return enc
    except (ValueError, LookupError):
        pass
    return "utf-8"  # fallback (Unix / English Windows)


# A5 fix: whitelist environment variables — only pass known-safe variables to child processes
# Replaces the old blacklist approach (blacklist always has gaps, new key prefixes can leak)
_ENV_WHITELIST_PREFIXES: tuple[str, ...] = (
    # Path / Shell
    "PATH", "HOME", "USER", "USERNAME", "LOGNAME", "SHELL", "TEMP", "TMP",
    "TMPDIR", "PWD", "OLDPWD", "TERM", "COLORTERM",
    # Language / Encoding
    "LANG", "LC_", "LANGUAGE", "POSIXLY_CORRECT",
    # Build tools
    "CC", "CXX", "CFLAGS", "CXXFLAGS", "LDFLAGS", "LD_LIBRARY_PATH",
    "MAKEFLAGS", "CMAKE_", "GRADLE_", "MAVEN_", "ANT_",
    # Node / npm
    "NODE_", "NPM_", "YARN_", "PNPM_",
    # Python
    "PYTHON", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
    "PIP_", "VIRTUAL_ENV", "CONDA_", "POETRY_",
    # Rust
    "CARGO_", "RUST_", "RUSTUP_",
    # Go
    "GOPATH", "GOROOT", "GOARCH", "GOOS", "GO111MODULE",
    # Git
    "GIT_", "GIT_CONFIG_",
    # SSH
    "SSH_", "SSH_AUTH_SOCK",
    # CI / Platform
    "CI", "GITHUB_", "GITLAB_", "ACTIONS_",
    "DOCKER_", "DOCKER_HOST",
    # Proxy
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "ALL_PROXY",
    # System
    "HOSTNAME", "HOSTTYPE", "MACHTYPE", "OSTYPE", "ARCH",
    "DISPLAY", "WAYLAND_", "XDG_",
    "COMSPEC", "PATHEXT", "PROCESSOR_", "NUMBER_OF_PROCESSORS",
    "OS", "SYSTEMROOT", "WINDIR",
    # Editor / diff tools
    "EDITOR", "VISUAL", "PAGER", "LESS", "MORE", "DIFF_",
)


def _is_env_whitelisted(key: str) -> bool:
    """A5: check if env var key is whitelisted (prefix match)."""
    for prefix in _ENV_WHITELIST_PREFIXES:
        if key.upper().startswith(prefix.upper()):
            return True
    return False


# v0.0.22: PowerShell 5.1 (Windows default) does not support `&&` / `||` command
# separators (PS 7+ supports them). Weak models often write `cmd1 && cmd2`,
# which causes ParserError on PS 5.1.
# Translate `a && b && c` to `a; if ($?) { b; if ($?) { c } }` preserving conditional
# execution semantics. `||` translates to `; if (-not $?) { ... }`.
# B7 fix: quote-aware — skip && / || inside quotes to avoid falsely splitting
# `echo "a && b"`.
_AND_CHAIN_RE = re.compile(r'\s&&\s')
_OR_CHAIN_RE = re.compile(r'\s\|\|\s')


def _split_operator_aware(command: str, pattern: re.Pattern[str]) -> list[str]:
    """Quote-aware operator splitting: only split && / || outside quotes.

    Uses re.finditer to locate pattern matches, then checks if each match
    falls inside quotes by scanning the text before it. This avoids the
    bug of comparing raw regex pattern strings against command text.

    B8 fix: use re.finditer instead of character-by-character pattern matching.
    """
    parts: list[str] = []
    prev_end = 0

    for m in pattern.finditer(command):
        start, end = m.start(), m.end()

        # Check if this match is inside quotes by scanning text before it
        text_before = command[:start]
        in_single = False
        in_double = False
        for ch in text_before:
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double

        if in_single or in_double:
            continue  # Skip matches inside quotes

        # Add text before this match
        segment = command[prev_end:start].strip()
        if segment:
            parts.append(segment)
        prev_end = end

    remaining = command[prev_end:].strip()
    if remaining:
        parts.append(remaining)

    return parts if parts else [command]


def _translate_chain_for_ps5(command: str) -> str:
    """Translate bash `&&` / `||` to PowerShell 5.1 compatible conditional execution.

    PS 5.1 does not support `&&` / `||` (PS 7+ supports them). Translation rules:
      - `a && b`  -> `a; if ($?) { b }`
      - `a || b`  -> `a; if (-not $?) { b }`
    Nested: `a && b && c` -> `a; if ($?) { b; if ($?) { c } }`
    B7: quote-aware -- do not split && / || inside quotes.
    """
    # Process && first (higher priority), then ||
    parts = _split_operator_aware(command, _AND_CHAIN_RE)
    if len(parts) == 1:
        # No &&, check for ||
        parts = _split_operator_aware(command, _OR_CHAIN_RE)
        if len(parts) == 1:
            return command
        result = parts[0]
        for part in parts[1:]:
            result += f'; if (-not $?) {{ {part} }}'
        return result

    # 递归翻译首段 (修复: 首段可能包含 || 未被翻译)
    result = _translate_chain_for_ps5(parts[0])
    for part in parts[1:]:
        # Recursively process || inside the part (mixed chains: a && b || c)
        part_translated = _translate_chain_for_ps5(part)
        result += f'; if ($?) {{ {part_translated} }}'
    return result


# O6: cache sanitized environment variables, avoids copying os.environ on every bash call.
# Environment variables rarely change during process lifetime, so a single cache is sufficient.
# v0.1.2: thread lock protection; B2: unified build inside single lock, eliminates race window.
_ENV_CACHE: dict[str, str] | None = None
_ENV_CACHE_LOCK = threading.Lock()


def reset_env_cache() -> None:
    """v0.5.0: 重置环境变量缓存, 用于测试隔离。"""
    global _ENV_CACHE
    _ENV_CACHE = None


def clear_env_cache() -> None:
    """Clear environment variable cache (test isolation / sub-agent fork use).
    Thread-safe (lock protected).

    Call between tests to ensure _sanitize_env re-reads os.environ.
    """
    global _ENV_CACHE
    with _ENV_CACHE_LOCK:
        _ENV_CACHE = None


def _sanitize_env() -> dict[str, str]:
    """Build sanitized environment variables.
    A5 fix: uses whitelist mode -- only passes known-safe variables, rejects all unknown keys.

    Child bash processes inherit the parent's environ; blacklist mode always has gaps
    (new key prefixes can appear). Whitelist mode is safer: only passes PATH / HOME /
    build tools and other known-safe variables.
    Cached: built once on first module call, returns cached copy on subsequent calls.
    """
    global _ENV_CACHE
    # Single lock protection: read + write both inside the lock, eliminates race window
    with _ENV_CACHE_LOCK:
        if _ENV_CACHE is not None:
            return dict(_ENV_CACHE)

        safe_env = {}
        for key, val in os.environ.items():
            if _is_env_whitelisted(key):
                safe_env[key] = val

        _ENV_CACHE = safe_env
        return dict(_ENV_CACHE)


# v0.0.23: self-protection mode -- detects commands attempting to terminate the zall process.
# These regexes match PID or process-name references in commands, preventing the agent from
# accidentally killing itself.
# Even if context_judge rules are bypassed (e.g., custom rules.toml), this check acts as the
# last line of defense.
# v0.5.0: _SELF_PID is lazily computed to avoid stale PID after fork.
_SELF_PID: int | None = None
# v0.1.2: process names (including python executables, prevents taskkill /IM name-based kill)
_SELF_PROCESS_NAMES = frozenset({"zall", "python", "python3", "py"})

# v0.5.0: 惰性计算 _SELF_PID, 每次访问时根据当前进程 ID 构建
# 避免 import 时固定 PID, 导致 fork 后子进程使用父进程 PID
_SELF_PID_PATTERNS_CACHE: tuple[re.Pattern[str], ...] | None = None
_SELF_PID_PATTERNS_LOCK = threading.Lock()


def _get_self_pid_patterns() -> tuple[re.Pattern[str], ...]:
    """获取匹配当前进程 PID 的正则表达式列表 (惰性 + 缓存)。"""
    global _SELF_PID, _SELF_PID_PATTERNS_CACHE
    pid = os.getpid()
    if _SELF_PID == pid and _SELF_PID_PATTERNS_CACHE is not None:
        return _SELF_PID_PATTERNS_CACHE
    with _SELF_PID_PATTERNS_LOCK:
        # Double-check after acquiring lock
        if _SELF_PID == pid and _SELF_PID_PATTERNS_CACHE is not None:
            return _SELF_PID_PATTERNS_CACHE
        _SELF_PID = pid
        _SELF_PID_PATTERNS_CACHE = (
            # taskkill /PID <pid> or taskkill /F /PID <pid>
            re.compile(rf"taskkill\s+.*?(?:/PID|/pid)\s+{_SELF_PID}\b", re.IGNORECASE),
            # taskkill /IM <name> (Windows kill by process name)
            *tuple(
                re.compile(rf"taskkill\s+.*?/IM\s+{re.escape(name)}\b", re.IGNORECASE)
                for name in _SELF_PROCESS_NAMES
            ),
            # tskill <pid>
            re.compile(rf"tskill\s+{_SELF_PID}\b", re.IGNORECASE),
            # tskill <name> (Windows kill by process name)
            *tuple(
                re.compile(rf"tskill\s+{re.escape(name)}\b", re.IGNORECASE)
                for name in _SELF_PROCESS_NAMES
            ),
            # kill -9 <pid> / kill <pid>
            re.compile(rf"kill\s+(?:-9\s+)?{_SELF_PID}\b"),
            # pkill -P <pid>
            re.compile(rf"pkill\s+.*?-P\s+{_SELF_PID}\b"),
            # Stop-Process -Id <pid> (PowerShell)
            re.compile(rf"Stop-Process\s+.*?-Id\s+{_SELF_PID}\b", re.IGNORECASE),
        )
        return _SELF_PID_PATTERNS_CACHE


def _check_self_protection(command: str) -> str | None:
    """Check if command contains dangerous operations (self-termination / system destruction).

    Returns None = safe; returns string = rejection reason (including matched pattern description).
    This is defense-in-depth: even if context_judge rules are bypassed, the tool layer rejects
    dangerous operations.

    v0.0.25 fix: uses "shutdown " (with space) instead of "shutdown", avoiding false positive
    on "where shutdown". Precisely matches execution scenarios, not probe/reference commands.
    """
    cmd_lower = command.lower().strip()

    # 1. Self-termination detection: command references the current zall process PID
    for pattern in _get_self_pid_patterns():
        if pattern.search(command):
            return (
                f"BLOCKED: command targets the current zall process (PID {_SELF_PID}). "
                f"To proceed, override through blacklist gate with explicit reason."
            )

    # 2. General dangerous commands (PID-independent)
    # v0.0.25: all keywords have trailing space, avoiding false positives like "where shutdown".
    # v0.0.32: uses command stem detection (extract first token), eliminates "format " false positive on "echo format".

    # Extract first command token (exclude shell variable assignments, comments, pre-pipe parts)
    first_token = cmd_lower.split(None, 1)[0] if cmd_lower.strip() else ""

    # Iterate first-token matches against rejected patterns (prevents false positives on "echo shutdown" / "where format")
    _DANGEROUS_FIRST_TOKEN: tuple[tuple[str, str], ...] = (
        ("shutdown", "system shutdown/restart"),
        ("format", "disk formatting"),
    )
    for keyword, desc in _DANGEROUS_FIRST_TOKEN:
        if first_token == keyword:
            return (
                f"BLOCKED: command contains dangerous operation ({desc}). "
                f"To proceed, override through blacklist gate with explicit reason."
            )

    # Iterate substring-matching patterns (compound commands, not first-token dependent)
    # These are multi-word/feature patterns that won't be triggered by first-token detection
    _DANGEROUS_PATTERNS: tuple[tuple[str, str], ...] = (
        ("shutdown /", "system shutdown/restart"),      # "shutdown /s" with space variant
        ("shutdown/", "system shutdown/restart"),       # "shutdown/s" no-space variant
        ("del /f /s", "recursive force delete"),
        ("del /f/s", "recursive force delete"),         # no-space variant
        ("rm -rf /", "recursive root delete"),
        ("dd if=", "disk overwrite"),
        ("mkfs.", "filesystem formatting"),             # "mkfs.ext4" etc.
        ("sc stop", "service control"),
        ("net stop", "service control"),
        (":(){ :|:& };:", "fork bomb"),
    )
    for keyword, desc in _DANGEROUS_PATTERNS:
        if keyword in cmd_lower:
            return (
                f"BLOCKED: command contains dangerous operation ({desc}). "
                f"To proceed, override through blacklist gate with explicit reason."
            )

    return None  # safe


def _truncate_at_bytes(text: str, max_bytes: int) -> str:
    """Truncate text by byte count (UTF-8), prefers breaking at newlines (v0.1.2).

    Uses UTF-8 for byte count since the output is sent to the API pipeline
    (which uses UTF-8). Subprocess output encoding is handled separately
    via _preferred_encoding() in _truncate_at_bytes_enc.
    """
    return _truncate_at_bytes_enc(text, max_bytes, "utf-8")


def _truncate_at_bytes_enc(text: str, max_bytes: int, encoding: str = "utf-8") -> str:
    """Truncate text by byte count with specified encoding (B3 fix).

    If text encodes to <= max_bytes, returns as-is.
    Otherwise truncates to within max_bytes, preferring the nearest newline break.
    """
    encoded = text.encode(encoding, errors="replace")
    if len(encoded) <= max_bytes:
        return text
    # 按字节truncate
    truncated = encoded[:max_bytes]
    # 尝试在最后一个换行符处断 (提高可读性)
    last_newline = truncated.rfind(b"\n")
    if last_newline > max_bytes // 2:  # 只在有足够内容时换行断
        truncated = truncated[:last_newline]
    return truncated.decode(encoding, errors="replace")


# ── CLIXML decoder (Windows PowerShell error output) ──

_CLIXML_HEADER = "#< CLIXML"


def _is_clixml(text: str) -> bool:
    """Check if text is CLIXML-encoded PowerShell output."""
    return text.lstrip().startswith(_CLIXML_HEADER)


def _decode_clixml(text: str) -> str:
    """Decode CLIXML-encoded PowerShell output to plain text.

    CLIXML is an XML format PowerShell uses for structured output.
    The format is:
      #< CLIXML
      <Objs ...><S S="Error">message</S><S S="Warning">...</S></Objs>

    Also decodes _xHHHH_ escape sequences used by PowerShell in CLIXML.

    Uses regex-based extraction (more robust than XML parsing when
    encoding is mismatched or characters are garbled).

    B8 fix: safety net — if PowerShell outputs CLIXML (e.g., parse errors
    on Windows), decode it so the agent can read the actual error message.
    """
    if not _is_clixml(text):
        return text

    # Use regex-based extraction (robust against encoding issues)
    return _extract_clixml_text_fallback(text)


_CLIXML_ESCAPE_RE = re.compile(r"_x([0-9A-Fa-f]{4})_")


def _decode_clixml_escapes(text: str) -> str:
    """Decode _xHHHH_ escape sequences in CLIXML text.

    PowerShell uses _xHHHH_ to encode characters that can't appear
    literally in XML (e.g., _x000D_ for CR, _x000A_ for LF).
    """
    def _replace(m: re.Match[str]) -> str:
        return chr(int(m.group(1), 16))
    return _CLIXML_ESCAPE_RE.sub(_replace, text)


def _extract_clixml_text_fallback(text: str) -> str:
    """Fallback: extract readable text from a CLIXML blob via regex.

    Used when XML parsing fails (malformed CLIXML).
    """
    lines: list[str] = []
    for m in re.finditer(r'<S[^>]*>(.*?)</S>', text, re.DOTALL):
        content = m.group(1)
        content = _decode_clixml_escapes(content)
        lines.append(content)
    if lines:
        return "\n".join(lines)
    # Last resort: strip XML tags, keep only text
    cleaned = re.sub(r'<[^>]+>', '', text)
    cleaned = re.sub(r'#< CLIXML\s*', '', cleaned)
    cleaned = _decode_clixml_escapes(cleaned.strip())
    return cleaned or text


class BashTool:
    """Execute shell command tool (ACI design).

    Uses a pluggable executor strategy (PopenExecutor by default,
    PtyExecutor for interactive commands).

    IPR-0 invariants:
        - always uses timeout (default 120s)
        - output exceeding MAX_OUTPUT_BYTES is truncated with notice
        - returns exit_code / stdout / stderr / duration

    Schema design:
        command: required, the command to execute
        timeout: optional, timeout in seconds (default 120, max 600)
        cwd:     optional, working directory (default: current directory)
    """

    __test__ = False

    def __init__(self, executor: BashExecutor | None = None) -> None:
        """Initialize with optional executor strategy.

        Args:
            executor: Executor to use. Defaults to PopenExecutor.
        """
        self._executor = executor or PopenExecutor()

    @property
    def tool_id(self) -> str:
        return "bash"

    @property
    def kind(self) -> ToolKind:
        return ToolKind.EXECUTE

    @property
    def capabilities(self):
        from zall.core.tool import ToolCapabilities, ToolScope
        return ToolCapabilities(is_read_only=False, tool_scope=ToolScope.Write)

    @property
    def schema(self) -> dict[str, Any]:
        # Cross-platform: detect shell per platform (Git Bash preferred on Windows)
        _shell = _detect_shell()
        _using_ps = _is_powershell(_shell)
        if sys.platform == "win32":
            if _using_ps:
                shell_hint = "bash-compatible (PowerShell)"
                cmd_hint = (
                    "On Windows the bash tool runs via PowerShell, so you can use "
                    "bash-compatible syntax: mkdir -p, single/double quoted strings, "
                    "&& / || / |, echo, cat, ls (as aliases). PowerShell cmdlets "
                    "(Get-ChildItem, Get-Content) also work."
                )
            else:
                shell_hint = "bash (Git Bash)"
                cmd_hint = (
                    "On Windows the bash tool runs via Git Bash, providing full "
                    "bash-compatible syntax: mkdir -p, single/double quoted strings, "
                    "&& / || / |, pipes, redirects, and standard Unix commands."
                )
        else:
            shell_hint = "bash"
            cmd_hint = "Use standard bash syntax (ls, cat, grep)."
        return {
            "type": "function",
            "function": {
                "name": "bash",
                "description": (
                    f"Execute a {shell_hint} command. Returns stdout, stderr, and exit code. "
                    "Commands run with a timeout (default 120s, max 600s). "
                    "Output is truncated at 50KB to prevent context pollution."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": f"The command to execute. {cmd_hint}",
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Timeout in seconds (default: 120, max: 600)",
                            "default": 120,
                        },
                        "cwd": {
                            "type": "string",
                            "description": "Working directory (default: current directory)",
                        },
                    },
                    "required": ["command"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        command = args.get("command", "")
        if not command:
            return ToolResult(
                success=False,
                output="[ERROR: command argument is required]",
                error="command required",
            )

        # Self-protection check (defense-in-depth)
        blocked = _check_self_protection(command)
        if blocked:
            return ToolResult(
                success=False,
                output=f"[ERROR: {blocked}]",
                error=blocked,
            )

        timeout = args.get("timeout", 120)
        if not isinstance(timeout, (int, float)) or timeout < 1:
            timeout = 120
        timeout = min(timeout, 600)

        cwd = args.get("cwd") or None

        return self._executor.execute(command, timeout, cwd=cwd)


# ═══════════════════════════════════════════════════════════════════
# PopenExecutor — Default subprocess-based executor
# ═══════════════════════════════════════════════════════════════════


class PopenExecutor:
    """Subprocess-based bash executor (default strategy).

    Uses subprocess.Popen with shell=True. Supports process group
    termination on timeout. This is the original BashTool behavior
    extracted into the executor strategy pattern.
    """

    def execute(
        self,
        command: str,
        timeout: int,
        cwd: str | None = None,
    ) -> ToolResult:
        """Execute a command via subprocess, return ToolResult.

        Cross-platform: detects the best available shell per platform.
        Windows: Git Bash preferred (schema hint), PowerShell (execution).
        macOS/Linux: /bin/bash.
        """
        if sys.platform == "win32":
            # Windows: PowerShell for execution (Git Bash detection used for schema hints)
            command = _translate_chain_for_ps5(command)
            ps_script = f"$ProgressPreference='SilentlyContinue'\n{command}"
            encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
            exec_args = f"powershell -NoProfile -EncodedCommand {encoded}"
        else:
            exec_args = command  # Unix: shell=True, command as-is

        start = time.monotonic()
        enc = _preferred_encoding()
        proc = None
        stdout = ""
        stderr = ""
        try:
            proc = subprocess.Popen(
                exec_args,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                text=True,
                encoding=enc,
                errors="replace",
                env=_sanitize_env(),
                start_new_session=True,
            )
            stdout, stderr = proc.communicate(timeout=timeout)

            # B8: decode CLIXML output on Windows (PowerShell error format)
            if sys.platform == "win32":
                if _is_clixml(stdout):
                    stdout = _decode_clixml(stdout)
                if _is_clixml(stderr):
                    stderr = _decode_clixml(stderr)
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            if proc is not None:
                try:
                    if sys.platform == "win32":
                        subprocess.run(
                            ["taskkill", "/F", "/PID", str(proc.pid), "/T"],
                            capture_output=True, timeout=5,
                        )
                    else:
                        import signal
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    pass
            return ToolResult(
                success=False,
                output=f"[ERROR: command timed out after {timeout}s]\n{duration:.1f}s elapsed",
                error=f"timeout after {timeout}s",
                artifacts={"duration": duration},
            )
        except OSError as e:
            return ToolResult(
                success=False,
                output=f"[ERROR: cannot execute command: {e}]",
                error=str(e),
            )

        duration = time.monotonic() - start
        exit_code = proc.returncode

        std_enc = _preferred_encoding()
        truncated = False
        stdout_bytes = len(stdout.encode(std_enc, errors="replace"))
        if stdout_bytes > MAX_OUTPUT_BYTES:
            stdout = _truncate_at_bytes_enc(stdout, MAX_OUTPUT_BYTES, std_enc) + (
                f"\n... [truncated: output too large ({stdout_bytes} bytes)]"
            )
            truncated = True
        stderr_bytes = len(stderr.encode(std_enc, errors="replace"))
        if stderr_bytes > MAX_OUTPUT_BYTES:
            stderr = _truncate_at_bytes_enc(stderr, MAX_OUTPUT_BYTES, std_enc) + (
                "\n... [stderr too large]"
            )

        output_parts = [f"exit_code: {exit_code}"]
        if stdout:
            output_parts.append(f"stdout:\n{stdout}")
        if stderr:
            output_parts.append(f"stderr:\n{stderr}")
        if truncated:
            output_parts.append("[Note: output was truncated]")

        return ToolResult(
            success=exit_code == 0,
            output="\n".join(output_parts),
            artifacts={
                "exit_code": exit_code,
                "duration": round(duration, 3),
                "stdout_bytes": stdout_bytes,
                "stderr_bytes": stderr_bytes,
                "truncated": truncated,
            },
        )