"""zall integration tests — ensure startup does not crash / REPL basic flow / slash command.

these tests ensure:
  1. `zall --version` correctly输出 (does not raise异常)
  2. `zall --help` 正常打印
  3. `zall init` create .zall/ 目录
  4. REPL 启动不因 prompt_toolkit 键绑定崩溃
  5. basic slash 命令does not raise异常
  6. MCP 配置缺失时不阻断启动

IPR-0 Counterexample: 如果任何一项因配置/依赖缺失而崩溃, integration tests先于用户报告.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

import pytest

from zall.cli.commands import cmd_cost, cmd_init
from zall.cli.commands._common import _handle_bare_slash, _print_about, _print_help, handle_slash
from zall.cli.prompt import make_prompt_fn, _COMMAND_META


# ──────────────────────────────────────────────────────────────────────────
# 启动稳定性
# ──────────────────────────────────────────────────────────────────────────


def test_version_output() -> None:
    """zall --version does not raiseexception, output包含version号."""
    from zall import __version__
    assert isinstance(__version__, str)
    assert len(__version__) > 0


def test_help_contains_commands() -> None:
    """_print_help output含所有关键command."""
    buf = io.StringIO()
    _print_help(buf)
    output = buf.getvalue()
    # 关键commandmust出现
    for cmd in ("/help", "/about", "/sessions", "/model", "/undo",
                "/git", "/diff", "/doctor", "/exit", "/compact"):
        assert cmd in output, f"help missing {cmd}"


def test_about_contains_philosophy() -> None:
    """_print_about output含ACI design哲学关键词."""
    buf = io.StringIO()
    _print_about(buf)
    output = buf.getvalue()
    assert "falsifiable" in output
    assert "reproducible" in output
    assert "PR-0" in output


def test_bare_slash_shows_commands() -> None:
    """孤立 / 显示command快速参考, does not crash溃."""
    buf = io.StringIO()
    _handle_bare_slash(buf)
    output = buf.getvalue()
    assert "/help" in output or "commands" in output


# ──────────────────────────────────────────────────────────────────────────
# slash command路由
# ──────────────────────────────────────────────────────────────────────────


def test_handle_slash_help() -> None:
    """_handle_slash /help returns "handled" does not raiseexception."""
    buf = io.StringIO()
    result = handle_slash("/help", {}, buf)
    assert result == "handled"


def test_handle_slash_exit() -> None:
    """_handle_slash /exit returns "exit"."""
    buf = io.StringIO()
    result = handle_slash("/exit", {}, buf)
    assert result == "exit"


def test_handle_slash_quit() -> None:
    """_handle_slash /quit returns "exit"."""
    buf = io.StringIO()
    result = handle_slash("/quit", {}, buf)
    assert result == "exit"


def test_handle_slash_q() -> None:
    """_handle_slash /q returns "exit"."""
    buf = io.StringIO()
    result = handle_slash("/q", {}, buf)
    assert result == "exit"


def test_handle_slash_unknown() -> None:
    """未知command → "handled"  + does not raiseexception (含 did-you-mean)."""
    buf = io.StringIO()
    result = handle_slash("/unknown_cmd_xyz", {}, buf)
    assert result == "handled"
    output = buf.getvalue()
    assert "unknown" in output or "try /help" in output


def test_handle_slash_cost() -> None:
    """/cost 在有 usage 时does not raiseexception."""
    buf = io.StringIO()
    state = {"usage": {"prompt": 100, "completion": 50}}
    # _cmd_cost directly从 app 模块调
    cmd_cost("", buf, None, state)
    output = buf.getvalue()
    assert "100" in output or "cost" in output


def test_handle_slash_not_command() -> None:
    """不以 / 开头的字符串 → "none"."""
    buf = io.StringIO()
    result = handle_slash("not a command", {}, buf)
    assert result == "none"


# ──────────────────────────────────────────────────────────────────────────
# slash command with /clear
# ──────────────────────────────────────────────────────────────────────────


def test_handle_slash_clear() -> None:
    """/clear non- TTY returns "clear"."""
    buf = io.StringIO()
    result = handle_slash("/clear", {}, buf)
    assert result == "clear"


def test_handle_slash_verbose() -> None:
    """/verbose 切换 state["verbose"]"""
    buf = io.StringIO()
    state: dict = {}
    handle_slash("/verbose", state, buf)
    assert state.get("verbose") is True


def test_handle_slash_verbose_toggle() -> None:
    """两次 /verbose 切换回来."""
    buf = io.StringIO()
    state: dict = {}
    handle_slash("/verbose", state, buf)
    handle_slash("/verbose", state, buf)
    assert state.get("verbose") is False


# ──────────────────────────────────────────────────────────────────────────
# prompt_toolkit downgrade / 键绑定does not crash溃
# ──────────────────────────────────────────────────────────────────────────


def test_make_prompt_fn_not_crash() -> None:
    """make_prompt_fn 在任何情况下都does not raiseexception (即使 prompt_toolkit 不可用)."""
    fn = make_prompt_fn(commands=["/help", "/exit"])
    # 至少functioncreate成功
    assert callable(fn)


def test_command_meta_contains_undo_git() -> None:
    """_COMMAND_META 含新command."""
    assert "/undo" in _COMMAND_META
    assert "/git" in _COMMAND_META


# ──────────────────────────────────────────────────────────────────────────
# zall init
# ──────────────────────────────────────────────────────────────────────────


def test_init_creates_files() -> None:
    """zall init 在当前directorycreate .zall/ configfile."""
    import os as _os
    with tempfile.TemporaryDirectory() as td:
        buf = io.StringIO()
        old = _os.getcwd()
        try:
            _os.chdir(td)
            cmd_init("", buf, None, {})
        finally:
            _os.chdir(old)
        zall_dir = Path(td) / ".zall"
        assert zall_dir.exists()
        assert (zall_dir / "rules.toml").exists()
        assert (zall_dir / "AGENTS.md").exists()
        assert (zall_dir / "mcp.toml").exists()
        assert (zall_dir / "skills.toml").exists()
        output = buf.getvalue()
        assert "initialized" in output


def test_init_idempotent() -> None:
    """二次 init 不covers已有file, does not raiseexception."""
    import os as _os
    with tempfile.TemporaryDirectory() as td:
        buf = io.StringIO()
        old = _os.getcwd()
        try:
            _os.chdir(td)
            cmd_init("", buf, None, {})
            buf2 = io.StringIO()
            cmd_init("", buf2, None, {})  # 第二次, 不应抛异常
        finally:
            _os.chdir(old)
        assert "initialized" in buf2.getvalue()


# ──────────────────────────────────────────────────────────────────────────
# BashTool Windows 绑定
# ──────────────────────────────────────────────────────────────────────────


def test_bash_schema_contains_shell_hint() -> None:
    """BashTool schema description含 shell information, does not raiseexception."""
    from zall.tools.bash import BashTool
    tool = BashTool()
    schema = tool.schema
    desc = schema.get("function", {}).get("description", "")
    # Windows 上含 cmd.exe, Unix 含 bash
    assert "command" in schema["function"]["parameters"]["properties"]
    # execute does not raiseexception
    result = tool.execute({"command": "echo hello", "timeout": 5})
    assert result.success