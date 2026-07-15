"""edit_file + bash invariant tests."""

from __future__ import annotations

import os
import tempfile

import pytest

from zall.tools.edit_file import EditFileTool
from zall.tools.bash import BashTool


# ── edit_file ──

@pytest.fixture
def edit_tool() -> EditFileTool:
    return EditFileTool()


@pytest.fixture
def tmp_file() -> str:
    p = tempfile.mktemp(suffix=".py")
    with open(p, "w") as f:
        f.write("def hello():\n    return 'world'\n\n# end\n")
    yield p
    try:
        os.unlink(p)
    except OSError:
        pass


class TestEditFileTool:
    def test_unique_replace(self, edit_tool: EditFileTool, tmp_file: str) -> None:
        result = edit_tool.execute({"path": tmp_file, "old_string": "return 'world'", "new_string": "return 'zall'"})
        assert result.success is True
        with open(tmp_file) as f:
            assert "zall" in f.read()

    def test_multiple_matches_fails(self, edit_tool: EditFileTool, tmp_file: str) -> None:
        """Counterexample: multiple matches → fail + show all positions."""
        result = edit_tool.execute({"path": tmp_file, "old_string": "e", "new_string": "X"})
        assert result.success is False
        assert "multiple matches" in result.output.lower() or "matched" in result.output.lower()

    def test_not_found_fails(self, edit_tool: EditFileTool, tmp_file: str) -> None:
        """Counterexample: 未找到 → fail."""
        result = edit_tool.execute({"path": tmp_file, "old_string": "nonexistent_xyz", "new_string": "X"})
        assert result.success is False

    def test_file_not_found(self, edit_tool: EditFileTool) -> None:
        result = edit_tool.execute({"path": "/nonexistent/xyz.py", "old_string": "x", "new_string": "y"})
        assert result.success is False

    def test_empty_old_string(self, edit_tool: EditFileTool, tmp_file: str) -> None:
        """Counterexample: old_string for空 → reject."""
        result = edit_tool.execute({"path": tmp_file, "old_string": "", "new_string": "X"})
        assert result.success is False


# ── bash ──

@pytest.fixture
def bash_tool() -> BashTool:
    return BashTool()


class TestBashTool:
    def test_echo(self, bash_tool: BashTool) -> None:
        # v0.0.22: Windows 上 bash 走 PowerShell, echo 是 Write-Output 别名,
        # 多词parameter会被当数组output (每词一行).用双引号确保单行output.
        result = bash_tool.execute({"command": 'echo "hello zall"'})
        assert result.success is True
        assert "hello zall" in result.output

    def test_exit_code_nonzero(self, bash_tool: BashTool) -> None:
        """Counterexample: commandfail → success=False."""
        result = bash_tool.execute({"command": "exit 1"})
        assert result.success is False
        assert result.artifacts["exit_code"] == 1

    def test_timeout(self, bash_tool: BashTool) -> None:
        """Counterexample: timeout → returns timeout error.跨平台用 python 睡眠触发 timeout."""
        import sys
        result = bash_tool.execute(
            {"command": f"{sys.executable} -c \"import time; time.sleep(10)\"", "timeout": 1}
        )
        assert result.success is False
        assert "timeout" in result.output.lower() or "timed out" in result.output.lower()

    def test_empty_command(self, bash_tool: BashTool) -> None:
        result = bash_tool.execute({"command": ""})
        assert result.success is False

    def test_stderr_captured(self, bash_tool: BashTool) -> None:
        """Happy path: stderr 被捕获."""
        result = bash_tool.execute({"command": "echo error >&2"})
        # exit 0, success=True, stderr 在 output 中
        assert "error" in result.output.lower() or "stderr" in result.output.lower()

    def test_artifacts(self, bash_tool: BashTool) -> None:
        result = bash_tool.execute({"command": "echo hi"})
        assert result.success is True
        assert "exit_code" in result.artifacts
        assert "duration" in result.artifacts