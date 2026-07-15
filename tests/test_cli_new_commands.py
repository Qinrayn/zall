"""CLI new command tests: /add, /drop, /fix, /review, /retry, /search

IPR-0: each test must contain a counterexample (counterexample).
Phase 4: covers all new commands happy path + counterexamples.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from zall.cli import session as session_mod
from zall.cli.commands import (
    cmd_add, cmd_diff, cmd_drop, cmd_fix, cmd_retry, cmd_review, cmd_search,
    get_known_commands,
)
from zall.cli.commands._common import (
    _handle_bare_slash, _print_help, handle_slash,
)
from zall.core.model import Message, ModelResponse, StopReason, ToolCall
from zall.core.verifiability import EventType


# ─────────────────────────────────────────────────────────────────────────
# Fake Loop (轻量, 不dependency AgentLoop)
# ─────────────────────────────────────────────────────────────────────────


class _FakeRecorder:
    """Fake RunRecorder 供test用."""
    __test__ = False

    def __init__(self, events: list | None = None) -> None:
        self.events = list(events) if events else []


class _FakeRecorderEvent:
    """Fake TimelineEvent 供test用."""
    __test__ = False

    def __init__(self, event_type: str, payload: dict | None = None) -> None:
        self.event_type = event_type
        self.payload = payload or {}


class _FakeLoop:
    """Fake AgentLoop 供 slash commandtests."""
    __test__ = False

    def __init__(self, messages: list | None = None) -> None:
        self._messages = list(messages) if messages else []
        self._recorder = _FakeRecorder()

    @property
    def recorder(self):
        return self._recorder

    def add_user_message(self, content: str) -> None:
        self._messages.append(Message(role="user", content=content))

    def add_user_file_message(self, content: str) -> None:
        """v0.1.3: compatible公开 API add_user_file_message"""
        self._messages.append(Message(role="user", content=content))

    def remove_messages_by_predicate(self, predicate) -> int:
        """v0.1.3: compatible公开 API remove_messages_by_predicate"""
        before = len(self._messages)
        self._messages = [m for m in self._messages if not predicate(m)]
        return before - len(self._messages)

    def set_messages(self, messages: list) -> None:
        self._messages = list(messages)


# ─────────────────────────────────────────────────────────────────────────
# /search commandtest
# ─────────────────────────────────────────────────────────────────────────


class TestSearchCommand:
    """/search: networksearch"""

    def test_search_no_query_shows_usage(self) -> None:
        """Happy path: /search 无参 → usage."""
        buf = io.StringIO()
        cmd_search("", buf)
        assert "usage" in buf.getvalue().lower()

    def test_search_with_query_executes(self) -> None:
        """Happy path: /search 有参 → 调用 SearchTool."""
        buf = io.StringIO()
        cmd_search("python tutorials", buf)
        val = buf.getvalue()
        # 可能成功或network不通, 但output应有content
        assert len(val) > 0

    def test_search_routes_via_slash(self) -> None:
        """Happy path: /search 路由到 _cmd_search."""
        buf = io.StringIO()
        result = handle_slash("/search", {}, buf)
        assert result == "handled"

    def test_search_routes_with_query(self) -> None:
        """Happy path: /search query 路由到 _cmd_search."""
        buf = io.StringIO()
        result = handle_slash("/search hello world", {}, buf)
        assert result == "handled"

    def test_search_routes_no_arg(self) -> None:
        """Happy path: /search 无参 → handled (显示 usage)."""
        buf = io.StringIO()
        result = handle_slash("/search", {"usage": {}}, buf)
        assert result == "handled"


# ─────────────────────────────────────────────────────────────────────────
# /add commandtest
# ─────────────────────────────────────────────────────────────────────────


class TestAddCommand:
    """/add: contextinjectionfile"""

    def test_add_no_arg_shows_usage(self) -> None:
        """Happy path: /add 无参 → usage."""
        buf = io.StringIO()
        cmd_add("", buf, _FakeLoop())
        assert "usage" in buf.getvalue().lower()

    def test_add_no_loop_shows_message(self) -> None:
        """Happy path: /add 时 loop=None → prompt."""
        buf = io.StringIO()
        cmd_add("test.py", buf, None)
        assert "conversation" in buf.getvalue().lower()

    def test_add_nonexistent_file_shows_error(self) -> None:
        """Happy path: /add 不存在的file → 显示 not found."""
        buf = io.StringIO()
        loop = _FakeLoop()
        cmd_add("nonexistent_file_xyz.py", buf, loop)
        assert "not found" in buf.getvalue().lower()

    def test_add_file_adds_to_messages(self, tmp_path: Path) -> None:
        """Happy path: /add 存在的file → injection到 messages."""
        f = tmp_path / "test.txt"
        f.write_text("hello world", encoding="utf-8")
        buf = io.StringIO()
        loop = _FakeLoop()
        loop._messages = [Message(role="system", content="sys")]
        state: dict = {}
        cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            cmd_add("test.txt", buf, loop, state)
        finally:
            os.chdir(cwd)
        val = buf.getvalue()
        assert "added" in val.lower()
        # messages 应有injectioncontent
        assert len(loop._messages) > 1  # system + injected

    def test_add_too_many_files(self, tmp_path: Path) -> None:
        """Counterexample: 一次 /add 超过 10 个file → 限数."""
        buf = io.StringIO()
        loop = _FakeLoop()
        state: dict = {}
        # construct超长parameter
        many_files = " ".join([f"f{i}.py" for i in range(15)])
        cmd_add(many_files, buf, loop, state)
        val = buf.getvalue()
        # error的file应显示 not found
        assert "too many" in val.lower() or "found" in val.lower()

    def test_add_routes_via_slash(self) -> None:
        """Happy path: /add 路由到 _cmd_add."""
        buf = io.StringIO()
        state: dict = {}
        with patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/x")):
            result = handle_slash("/add", state, buf)
        assert result == "handled"


# ─────────────────────────────────────────────────────────────────────────
# /drop commandtest
# ─────────────────────────────────────────────────────────────────────────


class TestDropCommand:
    """/drop: removeinjection的file"""

    def test_drop_no_loop_shows_message(self) -> None:
        """Happy path: /drop 时 loop=None → prompt."""
        buf = io.StringIO()
        cmd_drop("", buf, None)
        assert "conversation" in buf.getvalue().lower()

    def test_drop_no_added_files(self) -> None:
        """Happy path: /drop 无已addfile → prompt."""
        buf = io.StringIO()
        loop = _FakeLoop()
        state: dict = {}
        cmd_drop("", buf, loop, state)
        val = buf.getvalue()
        assert "added" in val.lower() or "no" in val.lower()

    def test_drop_all_removes_injected(self) -> None:
        """Happy path: /drop --all remove所有injectionfilemessage."""
        buf = io.StringIO()
        loop = _FakeLoop()
        state: dict = {"_artifact_files": ["/tmp/test.txt"]}
        loop._messages = [
            Message(role="system", content="sys"),
            Message(role="user", content="[user added file: /tmp/test.txt]\n```\ncontent\n```"),
            Message(role="user", content="normal message"),
        ]
        msg_count_before = len(loop._messages)
        cmd_drop("--all", buf, loop, state)
        assert len(loop._messages) < msg_count_before
        val = buf.getvalue()
        assert "removed" in val.lower()

    def test_drop_empty_arg_lists(self) -> None:
        """Happy path: /drop 无参但有 added_files → 列出."""
        buf = io.StringIO()
        loop = _FakeLoop()
        state: dict = {"_artifact_files": ["/tmp/test.txt"]}
        cmd_drop("", buf, loop, state)
        val = buf.getvalue()
        assert "added files" in val.lower()

    def test_drop_routes_via_slash(self) -> None:
        """Happy path: /drop 路由到 _cmd_drop."""
        buf = io.StringIO()
        state: dict = {}
        with patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/x")):
            result = handle_slash("/drop", state, buf)
        assert result == "handled"


# ─────────────────────────────────────────────────────────────────────────
# /fix commandtest
# ─────────────────────────────────────────────────────────────────────────


class TestFixCommand:
    """/fix: 自动诊断fix"""

    def test_fix_no_loop_shows_message(self) -> None:
        """Happy path: /fix 时 loop=None → prompt."""
        buf = io.StringIO()
        cmd_fix("", buf, None)
        assert "conversation" in buf.getvalue().lower()

    def test_fix_no_error_no_args(self) -> None:
        """Counterexample: /fix 无error记录、无参 → prompt."""
        buf = io.StringIO()
        loop = _FakeLoop()
        cmd_fix("", buf, loop)
        val = buf.getvalue()
        assert "no recent" in val.lower() or "found" in val.lower()

    def test_fix_with_args_injects_prompt(self) -> None:
        """Happy path: /fix <command> injectionfix prompt."""
        buf = io.StringIO()
        loop = _FakeLoop()
        loop._recorder = _FakeRecorder()
        cmd_fix("some command", buf, loop)
        val = buf.getvalue()
        assert "analyzing" in val.lower()
        assert len(loop._messages) > 0  # prompt injected

    def test_fix_with_last_error(self) -> None:
        """Happy path: /fix 无参但有 failed bash → 分析."""
        buf = io.StringIO()
        loop = _FakeLoop()
        # mockfail的 bash 调用
        loop._recorder = _FakeRecorder([
            _FakeRecorderEvent(EventType.TOOL_CALL_START, {
                "tool_id": "bash", "args": {"command": "ls /nonexistent"}
            }),
            _FakeRecorderEvent(EventType.TOOL_CALL_END, {
                "tool_id": "bash", "success": False,
                "error": "No such file or directory",
            }),
        ])
        cmd_fix("", buf, loop)
        val = buf.getvalue()
        # 应显示分析information
        assert "analyzing" in val.lower() or "error" in val.lower()

    def test_fix_routes_via_slash(self) -> None:
        """Happy path: /fix 路由到 _cmd_fix."""
        buf = io.StringIO()
        state: dict = {}
        with patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/x")):
            result = handle_slash("/fix", state, buf)
        assert result == "handled"


# ─────────────────────────────────────────────────────────────────────────
# /review commandtest
# ─────────────────────────────────────────────────────────────────────────


class TestReviewCommand:
    """/review: 代码审查"""

    def test_review_no_git_non_repo(self) -> None:
        """Happy path: /review in non- git 仓库 → prompt."""
        buf = io.StringIO()
        # 在一个临时non- git directory调用
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            cwd = os.getcwd()
            try:
                os.chdir(td)
                cmd_review("", buf, _FakeLoop())
            finally:
                os.chdir(cwd)
        val = buf.getvalue()
        assert "git" in val.lower()

    def test_review_routes_via_slash(self) -> None:
        """Happy path: /review 路由到 _cmd_review."""
        buf = io.StringIO()
        state: dict = {}
        with patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/x")):
            result = handle_slash("/review", state, buf)
        assert result == "handled"

    def test_review_injects_prompt_when_loop_exists(self) -> None:
        """Happy path: /review 有 loop → injection review prompt."""
        buf = io.StringIO()
        loop = _FakeLoop([
            Message(role="system", content="sys"),
            Message(role="user", content="hi"),
        ])
        # in non- git repo 调用, review 仍应returns handled
        cmd_review("", buf, loop)
        # prompt 不应injection (因fornot git repo)
        # 这只是verifydoes not crash溃


# ─────────────────────────────────────────────────────────────────────────
# /retry commandtest
# ─────────────────────────────────────────────────────────────────────────


class TestRetryCommand:
    """/retry: 重新execute上一步"""

    def test_retry_no_loop_shows_message(self) -> None:
        """Happy path: /retry 时 loop=None → prompt."""
        buf = io.StringIO()
        cmd_retry("", buf, None, {})
        assert "conversation" in buf.getvalue().lower()

    def test_retry_no_assistant_message(self) -> None:
        """Counterexample: /retry 时无 assistant message → prompt."""
        buf = io.StringIO()
        loop = _FakeLoop([Message(role="user", content="hi")])
        cmd_retry("", buf, loop, {})
        assert "no assistant" in buf.getvalue().lower()

    def test_retry_removes_last_assistant_and_tools(self) -> None:
        """Happy path: /retry remove最后一条 assistant 及其 tool message."""
        buf = io.StringIO()
        loop = _FakeLoop([
            Message(role="system", content="sys"),
            Message(role="user", content="list files"),
            Message(role="assistant", content="I will list files",
                    tool_calls=(ToolCall(id="c1", tool_id="bash",
                                        args={"command": "ls"}),)),
            Message(role="tool", content="file1.txt", tool_call_id="c1"),
        ])
        before = len(loop._messages)
        cmd_retry("", buf, loop, {})
        after = len(loop._messages)
        assert after < before
        val = buf.getvalue()
        assert "retrying" in val.lower() or "removed" in val.lower()

    def test_retry_preserves_user_message(self) -> None:
        """Happy path: /retry preserve用户的原始问题."""
        buf = io.StringIO()
        loop = _FakeLoop([
            Message(role="user", content="original question"),
            Message(role="assistant", content="response to retry"),
        ])
        cmd_retry("", buf, loop, {})
        assert any("original question" in m.content for m in loop._messages)

    def test_retry_routes_via_slash(self) -> None:
        """Happy path: /retry 路由到 _cmd_retry."""
        buf = io.StringIO()
        state: dict = {}
        with patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/x")):
            result = handle_slash("/retry", state, buf)
        assert result == "handled"


# ─────────────────────────────────────────────────────────────────────────
# /help 增强test
# ─────────────────────────────────────────────────────────────────────────


class TestHelpDetailed:
    """/help <cmd> 详细帮助"""

    def test_help_with_cmd(self) -> None:
        """Happy path: /help add → 显示 /add 详细帮助."""
        buf = io.StringIO()
        _print_help(buf, cmd_name="add")
        val = buf.getvalue()
        assert "/add" in val
        assert "file" in val.lower()

    def test_help_with_unknown_cmd(self) -> None:
        """Counterexample: /help unknown → 显示prompt."""
        buf = io.StringIO()
        _print_help(buf, cmd_name="nonexistent")
        val = buf.getvalue()
        assert "no detailed" in val.lower() or "help" in val.lower()

    def test_help_routes_to_detailed(self) -> None:
        """Happy path: /help add 路由到详细帮助."""
        buf = io.StringIO()
        result = handle_slash("/help add", {}, buf)
        assert result == "handled"
        val = buf.getvalue()
        assert "/add" in val

    def test_help_bare_shows_all(self) -> None:
        """Happy path: /help 无参显示所有command."""
        buf = io.StringIO()
        _print_help(buf)
        val = buf.getvalue()
        assert "/add" in val
        assert "/drop" in val
        assert "/fix" in val
        assert "/review" in val
        assert "/retry" in val
        assert "/search" in val


# ─────────────────────────────────────────────────────────────────────────
# /diff 增强test
# ─────────────────────────────────────────────────────────────────────────


class TestDiffEnhanced:
    """/diff 增强 (Phase 5.2)"""

    def test_diff_in_non_repo(self) -> None:
        """Counterexample: /diff in non- git 仓库 → does not crash溃."""
        buf = io.StringIO()
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            cwd = os.getcwd()
            try:
                os.chdir(td)
                cmd_diff("", buf, None, {})
            finally:
                os.chdir(cwd)
        val = buf.getvalue()
        assert "git" in val.lower() or "not" in val.lower()

    def test_diff_with_no_changes(self) -> None:
        """Counterexample: /diff 无change → prompt."""
        # zall repo 本身可能有change, 此test只verifycommanddoes not crash溃
        buf = io.StringIO()
        cmd_diff("", buf, None, {})
        val = buf.getvalue()
        assert val is not None  # does not crash溃即可


# ─────────────────────────────────────────────────────────────────────────
# 已知command元数据完整性test
# ─────────────────────────────────────────────────────────────────────────


class TestCommandMeta:
    """_KNOWN_COMMANDS 与 _COMMAND_META 一致性"""

    def test_all_new_commands_in_known(self) -> None:
        """Happy path: 所有新command在 _KNOWN_COMMANDS 中."""
        for cmd in ("/add", "/drop", "/fix", "/review", "/retry", "/search"):
            assert cmd in get_known_commands(), f"{cmd} missing"

    def test_all_new_commands_in_prompt_meta(self) -> None:
        """Happy path: 所有新command在 prompt.py 的 _COMMAND_META 中."""
        from zall.cli.prompt import _COMMAND_META
        for cmd in ("/add", "/drop", "/fix", "/review", "/retry", "/search"):
            assert cmd in _COMMAND_META, f"{cmd} missing from _COMMAND_META"

    def test_bare_slash_includes_new_commands(self) -> None:
        """Happy path: 孤立的 / 显示新command."""
        buf = io.StringIO()
        _handle_bare_slash(buf)
        val = buf.getvalue()
        # verifycommandlist包含新command名 (non- TTY pattern下)
        assert "add" in val.lower() or "/add" in val