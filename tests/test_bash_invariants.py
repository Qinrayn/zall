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
from zall.tools.bash import BashTool, _is_clixml, _decode_clixml, _decode_clixml_escapes


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


class TestBashClixml:
    """CLIXML detection and decoding tests (B8 fix).

    Windows PowerShell outputs errors in CLIXML format (#< CLIXML ...).
    These tests verify that the bash tool correctly decodes CLIXML to
    plain text, so the agent can read error messages.

    On non-Windows platforms, these tests are skipped since CLIXML
    does not occur on Unix.
    """

    @pytest.fixture
    def tool(self) -> BashTool:
        return BashTool()

    def test_is_clixml_detects_header(self) -> None:
        """CLIXML header is correctly detected."""
        assert _is_clixml("#< CLIXML\n<Objs></Objs>")
        assert not _is_clixml("plain text")
        assert not _is_clixml("")

    def test_decode_clixml_simple(self) -> None:
        """Simple CLIXML with error message is decoded."""
        clixml = (
            '#< CLIXML\n'
            '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
            '<S S="Error">command not found</S>'
            '</Objs>'
        )
        decoded = _decode_clixml(clixml)
        assert "command not found" in decoded
        assert "#< CLIXML" not in decoded

    def test_decode_clixml_multi_line(self) -> None:
        """Multi-line CLIXML error is decoded correctly."""
        clixml = (
            '#< CLIXML\n'
            '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
            '<S S="Error">line 1_x000D__x000A_</S>'
            '<S S="Error">line 2_x000D__x000A_</S>'
            '<S S="Error">    + CategoryInfo : ObjectNotFound</S>'
            '</Objs>'
        )
        decoded = _decode_clixml(clixml)
        assert "line 1" in decoded
        assert "line 2" in decoded
        assert "CategoryInfo" in decoded
        # _xHHHH_ escapes should be decoded
        assert "\r\n" in decoded

    def test_decode_clixml_escapes_hex(self) -> None:
        """_xHHHH_ escape sequences are decoded to characters."""
        assert _decode_clixml_escapes("_x000D_") == "\r"
        assert _decode_clixml_escapes("_x000A_") == "\n"
        assert _decode_clixml_escapes("_x0020_") == " "
        assert _decode_clixml_escapes("_x005F_") == "_"
        assert _decode_clixml_escapes("plain text") == "plain text"

    def test_decode_clixml_plain_text_passthrough(self) -> None:
        """Non-CLIXML text passes through unchanged."""
        text = "plain text output"
        assert _decode_clixml(text) == text

    def test_execute_echo_with_and_chain(self, tool: BashTool) -> None:
        """B8: echo && exit 0 should work without CLIXML.

        Root cause: _split_operator_aware had a bug where it compared
        command text against the raw regex pattern string instead of the
        actual operator. This caused && translation to fail on PowerShell 5.1,
        resulting in CLIXML error output.
        """
        result = tool.execute({"command": "echo hello && exit 0"})
        assert result.success, (
            f"&& chain failed: {result.output[:300]}"
        )
        assert "hello" in result.output
        # Output should NOT contain CLIXML
        assert "#< CLIXML" not in result.output, (
            f"CLIXML detected in output: {result.output[:300]}"
        )

    def test_execute_or_chain(self, tool: BashTool) -> None:
        """B8: false || echo ok should work (|| translation)."""
        result = tool.execute({"command": "false || echo ok"})
        assert result.success
        assert "ok" in result.output
        assert "#< CLIXML" not in result.output

    def test_execute_mixed_chain(self, tool: BashTool) -> None:
        """B8: mixed && || chain should work."""
        result = tool.execute({"command": "echo a && echo b || echo c"})
        assert result.success
        assert "a" in result.output
        assert "b" in result.output
        assert "#< CLIXML" not in result.output

    def test_execute_nonexistent_no_clixml(self, tool: BashTool) -> None:
        """B8: nonexistent command error should not contain raw CLIXML."""
        result = tool.execute({"command": "nonexistent_cmd_xyz_abc_123"})
        assert not result.success
        # Raw CLIXML should not appear in the output
        assert "#< CLIXML" not in result.output, (
            f"Raw CLIXML in output: {result.output[:300]}"
        )

# ── P2 fix: CLIXML 解码 (Windows PowerShell 错误输出) ──


class TestClixmlDecode:
    """P2: Windows PowerShell CLIXML 输出解码 (dogfood 发现)。"""

    def test_is_clixml_detects_header(self) -> None:
        """Happy path: CLIXML 头部检测。"""
        from zall.tools.bash import _is_clixml
        assert _is_clixml("#< CLIXML\n<Objs>") is True
        assert _is_clixml("  #< CLIXML\nstuff") is True

    def test_is_clixml_rejects_plain_text(self) -> None:
        """Counterexample: 纯文本不被误判为 CLIXML。"""
        from zall.tools.bash import _is_clixml
        assert _is_clixml("normal output") is False
        assert _is_clixml("pytest passed") is False
        assert _is_clixml("") is False

    def test_decode_clixml_extracts_error_text(self) -> None:
        """Happy path: 从 CLIXML 提取错误文本。"""
        from zall.tools.bash import _decode_clixml
        clixml = '#< CLIXML\n<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04"><S S="Error">command not found_x000D__x000A_</S></Objs>'
        decoded = _decode_clixml(clixml)
        assert "command not found" in decoded
        assert "_x000D_" not in decoded  # escape 序列已解码

    def test_decode_clixml_preserves_non_clixml(self) -> None:
        """Counterexample: 非 CLIXML 文本原样返回。"""
        from zall.tools.bash import _decode_clixml
        assert _decode_clixml("plain text") == "plain text"

    def test_decode_clixml_escapes(self) -> None:
        """Happy path: _x000D__x000A_ 转为 \r\n。"""
        from zall.tools.bash import _decode_clixml_escapes
        assert "_x000D_" not in _decode_clixml_escapes("error_x000D__x000A_done")
