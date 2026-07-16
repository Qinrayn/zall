"""Bash command semantics analysis for interpreting exit codes.

Deep analysis of bash command semantics: read-only / write / network / dangerous.
Replaces the old keyword-list matching approach.

Uses shlex parsing + redirect detection + command classification:
  - "echo hello > file.txt" is a write (has redirection)
  - "echo hello" is not a write (no redirection)
  - "cat file | grep pattern" is read-only (pipe of read-only commands)
  - "npm install" is a network operation (downloads packages)

IPR constraints:
  IPR-3: stdlib only (shlex + re), no model SDK
"""

from __future__ import annotations

import re
import shlex
from enum import Enum


class CommandSemantics(str, Enum):
    """command语义分class。"""
    READ_ONLY = "read_only"        # 只读: cat, ls, grep, find
    WRITE = "write"                # 写入: echo >, tee, sed -i, rm
    NETWORK = "network"            # 网络: curl, wget, npm install, pip install
    DANGEROUS = "dangerous"        # 危险: rm -rf /, dd, mkfs, shutdown
    UNKNOWN = "unknown"            # 未知/中性: echo, printf, cd


# 只读command集 (不修改filesystem)
_READ_ONLY_COMMANDS = frozenset({
    "cat", "head", "tail", "less", "more",
    "wc", "stat", "file", "strings", "xxd", "hexdump", "od",
    "jq", "awk", "cut", "sort", "uniq", "tr",  # 数据处理 (管道中常见)
    "grep", "rg", "ag", "ack", "find", "locate", "which", "whereis",
    "ls", "tree", "du", "df",
    "echo", "printf", "true", "false", ":",  # 中性输出
    "pwd", "whoami", "id", "hostname", "uname",
    "git", "diff", "git-log", "git-status", "git-branch",  # git 只读子命令
    "ps", "top", "lsof", "netstat", "ss",  # 系统状态
    "env", "printenv", "set",  # 环境变量
})

# writecommand集 (修改filesystem)
_WRITE_COMMANDS = frozenset({
    "tee", "mkdir", "touch", "cp", "mv", "ln",
    "chmod", "chown", "chattr",
    "sed",  # sed -i 会修改文件
    "git",  # git 有写子命令
})

# networkcommand集 (会发起networkrequest)
_NETWORK_COMMANDS = frozenset({
    "curl", "wget", "ftp", "scp", "rsync",
    "ssh", "telnet", "nc", "nmap",
})

# 需要检测子command的command
_SUBCOMMAND_COMMANDS = frozenset({"git", "npm", "pip", "pnpm", "yarn", "docker", "kubectl"})

# git 只读子command
_GIT_READ_ONLY = frozenset({
    "status", "log", "diff", "branch", "show", "blame", "ls-files",
    "ls-tree", "remote", "config", "rev-parse", "describe", "shortlog",
    "reflog", "stash", "list",
})

# npm/pip 只读子command
_NPM_READ_ONLY = frozenset({"list", "ls", "outdated", "view", "info", "search", "audit"})
_PIP_READ_ONLY = frozenset({"list", "show", "freeze", "search"})


# 重定向检测正则
_RE_REDIRECT_OUT = re.compile(r'(?:>>|>)\s*\S')
_RE_PIPE = re.compile(r'\|')


def analyze_command(command: str) -> CommandSemantics:
    """分析 bash command的语义。

    使用 shlex 解析管道链, 对每个子命令分类,
    返回整体语义 (最危险的优先)。

    优先级: DANGEROUS > WRITE > NETWORK > UNKNOWN > READ_ONLY
    """
    if not command or not command.strip():
        return CommandSemantics.UNKNOWN

    # 先check危险command (最优先)
    if _is_dangerous(command):
        return CommandSemantics.DANGEROUS

    # check重定向output (echo "x" > file 是write)
    if _RE_REDIRECT_OUT.search(command):
        return CommandSemantics.WRITE

    # 按pipeline分割, 分析每个子command
    try:
        # 用 shlex 分割pipeline
        pipe_parts = _RE_PIPE.split(command)
    except Exception:
        pipe_parts = [command]

    overall = CommandSemantics.READ_ONLY
    for part in pipe_parts:
        sem = _analyze_single_command(part.strip())
        if sem == CommandSemantics.DANGEROUS:
            return CommandSemantics.DANGEROUS
        if sem == CommandSemantics.WRITE:
            overall = CommandSemantics.WRITE
        elif sem == CommandSemantics.NETWORK:
            if overall != CommandSemantics.WRITE:
                overall = CommandSemantics.NETWORK
        elif sem == CommandSemantics.UNKNOWN:
            if overall == CommandSemantics.READ_ONLY:
                overall = CommandSemantics.UNKNOWN

    return overall


def _is_dangerous(command: str) -> bool:
    """检测危险command (rm -rf /, dd, mkfs, shutdown 等)。"""
    cmd_lower = command.lower().strip()
    # 提取首个command词
    first_token = cmd_lower.split(None, 1)[0] if cmd_lower.strip() else ""

    _DANGEROUS_FIRST = ("shutdown", "format", "dd", "mkfs")
    if first_token in _DANGEROUS_FIRST:
        return True

    _DANGEROUS_PATTERNS = (
        "rm -rf /", "rm -rf /*", "dd if=", "mkfs.",
        "shutdown/", "del /f /s", "del /f/s",
        ":(){ :|:& };:",  # fork bomb
    )
    for pattern in _DANGEROUS_PATTERNS:
        if pattern in cmd_lower:
            return True
    return False


def _analyze_single_command(cmd_str: str) -> CommandSemantics:
    """分析单个command (不含pipeline) 的语义。"""
    if not cmd_str:
        return CommandSemantics.READ_ONLY

    try:
        tokens = shlex.split(cmd_str)
    except ValueError:
        # shlex parse失败 (如未闭合引号), 退化为关键词匹配
        tokens = cmd_str.split()

    if not tokens:
        return CommandSemantics.READ_ONLY

    cmd = tokens[0]
    # 去除path前缀 (如 /usr/bin/cat → cat)
    if "/" in cmd:
        cmd = cmd.rsplit("/", 1)[-1]
    # Windows: 去除 .exe 后缀
    if cmd.endswith(".exe"):
        cmd = cmd[:-4]
    cmd_lower = cmd.lower()

    # 检测子command (git, npm, pip 等)
    if cmd_lower in _SUBCOMMAND_COMMANDS:
        subcmd = tokens[1] if len(tokens) > 1 else ""
        return _analyze_subcommand(cmd_lower, subcmd, tokens)

    if cmd_lower in _WRITE_COMMANDS:
        return CommandSemantics.WRITE

    if cmd_lower in _NETWORK_COMMANDS:
        return CommandSemantics.NETWORK

    if cmd_lower in _READ_ONLY_COMMANDS:
        return CommandSemantics.READ_ONLY

    # sed -i 是write, sed (无 -i) 是只读
    if cmd_lower == "sed":
        if "-i" in tokens or "--in-place" in tokens:
            return CommandSemantics.WRITE
        return CommandSemantics.READ_ONLY

    # rm 是write (remove)
    if cmd_lower in ("rm", "del", "rmdir", "unlink"):
        return CommandSemantics.WRITE

    # python/node 运行脚本 — 难以判断, return UNKNOWN
    if cmd_lower in ("python", "python3", "py", "node", "ruby", "perl"):
        return CommandSemantics.UNKNOWN

    # 未知command
    return CommandSemantics.UNKNOWN


def _analyze_subcommand(cmd: str, subcmd: str, tokens: list[str]) -> CommandSemantics:
    """分析带子command的command (git/npm/pip 等)。"""
    if cmd == "git":
        if subcmd in _GIT_READ_ONLY:
            return CommandSemantics.READ_ONLY
        # git stash list 是只读, git stash drop 是write
        if subcmd == "stash" and len(tokens) > 2 and tokens[2] in ("list", "show"):
            return CommandSemantics.READ_ONLY
        # git tag -l/--list 是只读, git tag <name> 是write
        if subcmd == "tag":
            if "-l" in tokens or "--list" in tokens:
                return CommandSemantics.READ_ONLY
            return CommandSemantics.WRITE
        # git add, commit, push, pull, merge, rebase 等是write
        if subcmd in ("add", "commit", "push", "pull", "merge", "rebase",
                       "checkout", "reset", "revert", "cherry-pick", "stash",
                       "branch"):
            return CommandSemantics.WRITE
        return CommandSemantics.READ_ONLY

    if cmd in ("npm", "pnpm", "yarn"):
        if subcmd in _NPM_READ_ONLY:
            return CommandSemantics.READ_ONLY
        if subcmd in ("install", "i", "add", "remove", "uninstall", "update",
                       "upgrade", "publish", "run"):
            return CommandSemantics.NETWORK if subcmd in ("install", "i", "add",
                       "update", "upgrade", "publish") else CommandSemantics.WRITE
        return CommandSemantics.UNKNOWN

    if cmd == "pip":
        if subcmd in _PIP_READ_ONLY:
            return CommandSemantics.READ_ONLY
        if subcmd in ("install", "uninstall", "download"):
            return CommandSemantics.NETWORK
        return CommandSemantics.UNKNOWN

    if cmd == "docker":
        if subcmd in ("ps", "images", "logs", "inspect", "stats", "top"):
            return CommandSemantics.READ_ONLY
        return CommandSemantics.WRITE

    if cmd == "kubectl":
        if subcmd in ("get", "describe", "logs", "top"):
            return CommandSemantics.READ_ONLY
        return CommandSemantics.WRITE

    return CommandSemantics.UNKNOWN


def get_semantics_label(sem: CommandSemantics) -> str:
    """获取语义标签 (供 UI 显示)。"""
    labels = {
        CommandSemantics.READ_ONLY: "read",
        CommandSemantics.WRITE: "write",
        CommandSemantics.NETWORK: "network",
        CommandSemantics.DANGEROUS: "dangerous",
        CommandSemantics.UNKNOWN: "unknown",
    }
    return labels.get(sem, "unknown")
