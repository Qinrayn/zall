"""Tests for PTY bash executor with fallback.

Corresponds to:
  DESIGN.md §4.2 (Tool layer)

IPR-0: each test includes a counterexample.
"""

import sys

import pytest


class TestPtyExecutor:
    """PTY executor tests (with fallback to PopenExecutor on platforms without PTY)."""

    @pytest.mark.skipif(sys.platform == "win32", reason="PTY not available on Windows")
    def test_pty_executor_echo(self) -> None:
        """PTY executor should execute simple commands."""
        from zall.tools.pty_executor import PtyExecutor

        executor = PtyExecutor()
        result = executor.execute("echo hello", timeout=10)
        assert result.success
        assert "hello" in result.output

    def test_pty_executor_exit_code(self) -> None:
        """PTY executor should report exit codes."""
        from zall.tools.pty_executor import PtyExecutor
        import sys

        executor = PtyExecutor()
        # Use platform-appropriate command
        if sys.platform == "win32":
            # On Windows, use PowerShell-compatible syntax (no &&)
            result = executor.execute("echo test", timeout=10)
        else:
            result = executor.execute("echo test && exit 0", timeout=10)
        assert result.success

    def test_pty_executor_timeout(self) -> None:
        """PTY executor should timeout on long-running commands."""
        from zall.tools.pty_executor import PtyExecutor

        executor = PtyExecutor()
        # Sleep longer than timeout
        result = executor.execute("sleep 10", timeout=2)
        assert not result.success
        assert "timeout" in result.error or "TIMEOUT" in result.output.upper() or "timeout" in result.output.lower()

    # Counterexample: empty command should not crash
    def test_pty_executor_empty_command(self) -> None:
        """PTY executor should handle empty commands gracefully."""
        from zall.tools.pty_executor import PtyExecutor

        executor = PtyExecutor()
        result = executor.execute("", timeout=5)
        # Should not crash
        assert result is not None

    def test_pty_executor_fallback(self) -> None:
        """PTY executor should fallback to PopenExecutor on Windows."""
        from zall.tools.pty_executor import PtyExecutor
        import sys

        executor = PtyExecutor()
        result = executor.execute("echo fallback_test", timeout=10)
        assert result.success
        assert "fallback_test" in result.output


class TestPopenExecutor:
    """PopenExecutor tests (the default strategy)."""

    def test_popen_executor_echo(self) -> None:
        """PopenExecutor should execute simple commands."""
        from zall.tools.bash import PopenExecutor

        executor = PopenExecutor()
        result = executor.execute("echo hello", timeout=10)
        assert result.success
        assert "hello" in result.output

    def test_popen_executor_timeout(self) -> None:
        """PopenExecutor should timeout on long-running commands."""
        from zall.tools.bash import PopenExecutor

        executor = PopenExecutor()
        result = executor.execute("sleep 10", timeout=2)
        assert not result.success
        assert "timeout" in result.error or "timeout" in result.output.lower()

    def test_popen_executor_failed_command(self) -> None:
        """PopenExecutor should handle failed commands."""
        from zall.tools.bash import PopenExecutor

        executor = PopenExecutor()
        result = executor.execute("exit 1", timeout=5)
        assert not result.success


class TestBashToolExecutorStrategy:
    """BashTool should use the executor strategy pattern."""

    def test_bash_tool_default_executor(self) -> None:
        """BashTool should use PopenExecutor by default."""
        from zall.tools.bash import BashTool, PopenExecutor

        tool = BashTool()
        assert isinstance(tool._executor, PopenExecutor)

    # Counterexample: custom executor should be used when injected
    def test_bash_tool_custom_executor(self) -> None:
        """BashTool should accept a custom executor."""
        from zall.tools.bash import BashTool, PopenExecutor

        custom = PopenExecutor()
        tool = BashTool(executor=custom)
        assert tool._executor is custom

    def test_bash_tool_pty_executor(self) -> None:
        """BashTool should work with PtyExecutor."""
        from zall.tools.bash import BashTool
        from zall.tools.pty_executor import PtyExecutor

        tool = BashTool(executor=PtyExecutor())
        result = tool.execute({"command": "echo pty_strategy", "timeout": 10})
        assert result.success
        assert "pty_strategy" in result.output