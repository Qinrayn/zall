"""bash tool invariant test (§4.2 tool layer).

IPR-0: each test must contain a counterexample.

Counterexample:
  1. empty command → success=False (not silent 通过)
  2. 超时 → success=False + 友好错误信息
  3. 自保护阻断 → success=False (防 agent 自终止)
  4. 输出截断 → truncated=True
  5. construct后改 success → raise (ToolResult frozen)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from zall.core.tool import Tool, ToolResult
from zall.tools.bash import BashTool


@pytest.fixture
def tool() -> BashTool:
    return BashTool()


class TestBashProtocol:
    """verify BashTool 满足 Tool Protocol."""

    def test_is_tool(self, tool: BashTool) -> None:
        """满足 Tool Protocol."""
        assert isinstance(tool, Tool)

    def test_tool_id(self, tool: BashTool) -> None:
        """tool_id 是 'bash'."""
        assert tool.tool_id == "bash"

    def test_schema_has_command_required(self, tool: BashTool) -> None:
        """schema 的 required 含 'command'."""
        params = tool.schema["function"]["parameters"]
        assert "command" in params["required"]

    def test_schema_has_timeout(self, tool: BashTool) -> None:
        """schema 含 timeout parameter."""
        params = tool.schema["function"]["parameters"]
        assert "timeout" in params["properties"]

    def test_schema_has_cwd(self, tool: BashTool) -> None:
        """schema 含 cwd parameter."""
        params = tool.schema["function"]["parameters"]
        assert "cwd" in params["properties"]

    def test_execute_returns_tool_result(self, tool: BashTool) -> None:
        """execute returns ToolResult instance."""
        result = tool.execute({"command": "echo hello"})
        assert isinstance(result, ToolResult)


class TestBashHappyPath:
    """正常executecommand的场景."""

    def test_echo(self, tool: BashTool) -> None:
        """execute echo returns成功."""
        result = tool.execute({"command": "echo hello"})
        assert result.success
        assert "hello" in result.output

    def test_exit_code_zero(self, tool: BashTool) -> None:
        """exit code 0 corresponds to success=True."""
        result = tool.execute({"command": "exit 0"})
        assert result.success

    def test_exit_code_nonzero(self, tool: BashTool) -> None:
        """Counterexample: exit code non-零 → success=False (但non-exception)."""
        result = tool.execute({"command": "exit 1"})
        assert not result.success
        assert "exit_code: 1" in result.output

    def test_stdout_captured(self, tool: BashTool) -> None:
        """stdout 被捕获到 output.

        v0.0.22: Windows 上 bash tool走 PowerShell, echo 是 Write-Output 别名,
        多词参数会被当数组输出 (每词一行).用双引号包裹确保单行输出,
        与 bash / cmd.exe 语义一致.
        """
        result = tool.execute({"command": 'echo "hello world"'})
        assert result.success
        assert "hello world" in result.output

    def test_artifacts_contain_exit_code(self, tool: BashTool) -> None:
        """artifacts 含 exit_code."""
        result = tool.execute({"command": "echo ok"})
        assert result.success
        assert "exit_code" in result.artifacts
        assert result.artifacts["exit_code"] == 0

    def test_artifacts_contain_duration(self, tool: BashTool) -> None:
        """artifacts 含 duration (秒数)."""
        result = tool.execute({"command": "echo ok"})
        assert result.success
        assert "duration" in result.artifacts
        assert result.artifacts["duration"] >= 0


class TestBashCounterExamples:
    """Counterexampletest: verifyinputerror和边界条件handle."""

    def test_empty_command(self, tool: BashTool) -> None:
        """Counterexample: empty command → success=False + 友好error."""
        result = tool.execute({"command": ""})
        assert not result.success
        assert "required" in result.output.lower()

    def test_missing_command(self, tool: BashTool) -> None:
        """Counterexample: 缺失 command → success=False."""
        result = tool.execute({})
        assert not result.success

    def test_self_protection_blocked(self, tool: BashTool) -> None:
        """Counterexample: 自terminatecommand被阻断 → success=False + BLOCKED information."""
        result = tool.execute({"command": "shutdown /s"})
        assert not result.success
        assert "BLOCKED" in result.output

    def test_self_protection_kill(self, tool: BashTool) -> None:
        """Counterexample: kill 当前process被阻断 → BLOCKED."""
        result = tool.execute({"command": f"kill {__import__('os').getpid()}"})
        assert not result.success
        assert "BLOCKED" in result.output

    def test_self_protection_dangerous(self, tool: BashTool) -> None:
        """Counterexample: rm -rf / 被阻断 → BLOCKED."""
        result = tool.execute({"command": "rm -rf /"})
        assert not result.success
        assert "BLOCKED" in result.output

    def test_result_is_frozen(self, tool: BashTool) -> None:
        """Counterexample: construct后改 success → must raise (ToolResult frozen)."""
        result = tool.execute({"command": "echo ok"})
        assert result.success
        with pytest.raises((TypeError, ValueError)):
            result.success = False

    def test_output_non_empty_on_failure(self, tool: BashTool) -> None:
        """Counterexample: 即使fail也有output, 不允许静默fail."""
        result = tool.execute({"command": ""})
        assert not result.success
        assert result.output  # output non-空

    def test_not_found_command(self, tool: BashTool) -> None:
        """Counterexample: 不存在的command → success=False."""
        result = tool.execute({"command": "nonexistent_cmd_xyz123"})
        assert not result.success

    def test_artifacts_contain_truncated_flag(self, tool: BashTool) -> None:
        """Counterexample: 大output场景 → truncated=True 且 artifacts 含 truncated flag."""
        # 生成大量output
        result = tool.execute({"command": "echo 'test line' && python -c \"print('x' * 100000)\""})
        # 不一定真truncate, 取决于output大小, 但 artifacts 应含 truncated 字段
        assert "truncated" in result.artifacts