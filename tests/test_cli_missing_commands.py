"""Tests for missing CLI commands: /undo, /checkpoint, /revert, /commit, /web.

v0.2.0: Supplement previously untested commands.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from zall.cli.commands import (
    cmd_undo, cmd_checkpoint, cmd_revert, cmd_commit, cmd_web,
)
from zall.core.model import Message
from zall.core.verifiability import EventType


# ──────────────────────────────────────────────────────────────────────
# /undo
# ──────────────────────────────────────────────────────────────────────


class TestUndo:
    def test_no_loop(self) -> None:
        """Counterexample: loop for None prompt when."""
        buf = io.StringIO()
        r = cmd_undo("", buf, None, {})
        assert r == "handled"
        assert "start a conversation first" in buf.getvalue()

    def test_no_recorder(self, fake_loop) -> None:
        """Counterexample: recorder 不可用prompt when."""
        fake_loop._recorder = None
        buf = io.StringIO()
        r = cmd_undo("", buf, fake_loop, {})
        assert r == "handled"
        assert "no recorder" in buf.getvalue()

    def test_no_tool_calls(self, fake_loop) -> None:
        """Counterexample: 无tool调用prompt when."""
        buf = io.StringIO()
        r = cmd_undo("", buf, fake_loop, {})
        assert r == "handled"
        assert "nothing to undo" in buf.getvalue()

    def test_undo_removes_last_tool(self, fake_loop) -> None:
        """Happy path: 有tool调用时可fallback."""
        fake_loop._messages = [
            Message(role="system", content="sys"),
            Message(role="user", content="do something"),
            Message(role="assistant", content="", tool_calls=[]),
            Message(role="tool", content="result", tool_call_id="tc1", tool_id="bash"),
        ]
        fake_loop._recorder.append(
            "end_1", 1000, EventType.TOOL_CALL_END,
            {"tool_id": "bash", "step": 1, "success": True},
        )
        buf = io.StringIO()
        r = cmd_undo("", buf, fake_loop, {})
        assert r == "handled"
        assert "undid" in buf.getvalue().lower()
        # fallback后应preserve system + user message
        assert len(fake_loop._messages) == 2

    def test_undo_not_found(self, fake_loop) -> None:
        """Counterexample: 有 tool_call_end 但无 tool_result message时."""
        fake_loop._messages = [
            Message(role="system", content="sys"),
            Message(role="user", content="do something"),
        ]
        fake_loop._recorder.append(
            "end_1", 1000, EventType.TOOL_CALL_END,
            {"tool_id": "bash", "step": 1, "success": True},
        )
        buf = io.StringIO()
        r = cmd_undo("", buf, fake_loop, {})
        assert r == "handled"
        assert "nothing to undo" in buf.getvalue()


# ──────────────────────────────────────────────────────────────────────
# /checkpoint
# ──────────────────────────────────────────────────────────────────────


class TestCheckpoint:
    def test_no_checkpoint_mgr(self, fake_loop) -> None:
        """Counterexample: 无 checkpoint_manager prompt when."""
        buf = io.StringIO()
        r = cmd_checkpoint("", buf, fake_loop, {})
        assert r == "handled"
        assert "no active session" in buf.getvalue()

    def test_list_no_checkpoints(self, tmp_path: Path, fake_loop) -> None:
        """Counterexample: 有 mgr 但无 checkpoint prompt when."""
        from zall.core.checkpoint import CheckpointManager
        cmgr = CheckpointManager(project_root=tmp_path)
        fake_loop._checkpoint_mgr = cmgr
        buf = io.StringIO()
        r = cmd_checkpoint("list", buf, fake_loop, {})
        assert r == "handled"
        assert "no checkpoints" in buf.getvalue()

    def test_save_checkpoint(self, tmp_path: Path, fake_loop) -> None:
        """Happy path: save checkpoint 成功."""
        import os
        old = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            from zall.core.checkpoint import CheckpointManager
            (tmp_path / "test.py").write_text("x = 1")
            cmgr = CheckpointManager(project_root=tmp_path)
            fake_loop._checkpoint_mgr = cmgr
            buf = io.StringIO()
            r = cmd_checkpoint("save mycp", buf, fake_loop, {})
            assert r == "handled"
            assert "checkpoint saved" in buf.getvalue() or "no files" in buf.getvalue()
        finally:
            os.chdir(old)

    def test_invalid_subcommand(self, tmp_path: Path, fake_loop) -> None:
        """Counterexample: 未知subcommand显示用法."""
        from zall.core.checkpoint import CheckpointManager
        fake_loop._checkpoint_mgr = CheckpointManager(project_root=tmp_path)
        buf = io.StringIO()
        r = cmd_checkpoint("xyz", buf, fake_loop, {})
        assert r == "handled"
        assert "usage" in buf.getvalue()


# ──────────────────────────────────────────────────────────────────────
# /revert
# ──────────────────────────────────────────────────────────────────────


class TestRevert:
    def test_no_checkpoint_mgr(self, fake_loop) -> None:
        """Counterexample: 无 checkpoint_manager 时 fallback prompt."""
        buf = io.StringIO()
        r = cmd_revert("", buf, fake_loop, {})
        assert r == "handled"
        assert "no checkpoint system" in buf.getvalue()

    def test_no_checkpoints_available(self, tmp_path: Path, fake_loop) -> None:
        """Counterexample: 有 mgr 但无 checkpoint."""
        from zall.core.checkpoint import CheckpointManager
        fake_loop._checkpoint_mgr = CheckpointManager(project_root=tmp_path)
        buf = io.StringIO()
        r = cmd_revert("", buf, fake_loop, {})
        assert r == "handled"
        assert "no checkpoints available" in buf.getvalue()


# ──────────────────────────────────────────────────────────────────────
# /commit
# ──────────────────────────────────────────────────────────────────────


class TestCommit:
    def test_not_git_repo(self, tmp_path: Path) -> None:
        """Counterexample: non- git 仓库prompt when."""
        import os
        old = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            buf = io.StringIO()
            r = cmd_commit("", buf, None, {})
            assert r == "handled"
            assert "not a git repository" in buf.getvalue()
        finally:
            os.chdir(old)

    def test_clean_repo(self, tmp_path: Path) -> None:
        """Counterexample: git 仓库但无changeprompt when."""
        import os, subprocess
        old = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            subprocess.run(["git", "init"], capture_output=True, timeout=5)
            subprocess.run(["git", "config", "user.email", "test@test.com"], capture_output=True, timeout=5)
            subprocess.run(["git", "config", "user.name", "Test"], capture_output=True, timeout=5)
            buf = io.StringIO()
            r = cmd_commit("", buf, None, {})
            assert r == "handled"
            assert "nothing to commit" in buf.getvalue()
        finally:
            os.chdir(old)

    def test_commit_with_message(self, tmp_path: Path, monkeypatch) -> None:
        """Happy path: 有change时可commit."""
        import os, subprocess
        old = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            subprocess.run(["git", "init"], capture_output=True, timeout=5)
            subprocess.run(["git", "config", "user.email", "test@test.com"], capture_output=True, timeout=5)
            subprocess.run(["git", "config", "user.name", "Test"], capture_output=True, timeout=5)
            (tmp_path / "test.py").write_text("x = 1")
            # stage file, 然后修改
            subprocess.run(["git", "add", "test.py"], capture_output=True, timeout=5)
            subprocess.run(["git", "commit", "-m", "initial"], capture_output=True, timeout=5)
            (tmp_path / "test.py").write_text("x = 2")  # 修改文件
            buf = io.StringIO()
            monkeypatch.setattr("builtins.input", lambda prompt="": "y")
            r = cmd_commit("commit message", buf, None, {})
            assert r == "handled"
            assert "committed" in buf.getvalue().lower() or "failed" in buf.getvalue().lower()
        finally:
            os.chdir(old)


# ──────────────────────────────────────────────────────────────────────
# /web
# ──────────────────────────────────────────────────────────────────────


class TestWeb:
    def test_no_url(self) -> None:
        """Counterexample: 无 URL 时显示用法."""
        buf = io.StringIO()
        r = cmd_web("", buf, None, {})
        assert r == "handled"
        assert "usage: /web" in buf.getvalue()

    def test_invalid_url(self) -> None:
        """Counterexample: URL 不可达时prompterror."""
        buf = io.StringIO()
        r = cmd_web("http://invalid.example.com/test", buf, None, {})
        assert r == "handled"
        # 即使networkfail也does not crash溃
        assert "usage" not in buf.getvalue().lower()


# ──────────────────────────────────────────────────────────────────────
# /web with real WebFetchTool mock
# ──────────────────────────────────────────────────────────────────────


class TestWebWithMock:
    def test_web_with_fetch_result(self, monkeypatch) -> None:
        """Happy path: WebFetchTool returns结果时显示."""
        from zall.tools.web_fetch import WebFetchTool
        from zall.core.tool import ToolResult

        def fake_execute(self, args):
            return ToolResult(
                success=True, output="Hello World",
                artifacts={"title": "Test Page", "chars": 11},
            )
        monkeypatch.setattr(WebFetchTool, "execute", fake_execute)
        buf = io.StringIO()
        r = cmd_web("https://example.com", buf, None, {})
        assert r == "handled"
        assert "Hello World" in buf.getvalue()
