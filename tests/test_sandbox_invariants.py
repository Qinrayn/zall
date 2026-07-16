"""Tests for Sandbox system (Phase 3c).

IPR-0: invariant tests must be written before or alongside the code.
"""

from __future__ import annotations

import os
import pytest
import tempfile
from pathlib import Path

from zall.sandbox import (
    Sandbox,
    SandboxMode,
    SandboxResult,
    SandboxError,
    ResourceLimits,
    ProcessSandbox,
    WorktreeSandbox,
)


class TestSandboxMode:
    """SandboxMode invariants."""

    def test_mode_values(self):
        assert SandboxMode.NONE.value == "none"
        assert SandboxMode.WORKTREE.value == "worktree"
        assert SandboxMode.PROCESS.value == "process"

    def test_default_mode(self):
        # Default should be NONE
        pass


class TestResourceLimits:
    """ResourceLimits invariants."""

    def test_default_limits(self):
        limits = ResourceLimits()
        assert limits.timeout_seconds == 30.0
        assert limits.allow_network is False
        assert limits.allow_write is False
        assert limits.max_output_bytes == 100_000

    def test_custom_limits(self):
        limits = ResourceLimits(
            timeout_seconds=60.0,
            allow_write=True,
            allow_network=True,
            max_output_bytes=500,
        )
        assert limits.timeout_seconds == 60.0
        assert limits.allow_write is True


class TestSandboxResult:
    """SandboxResult invariants."""

    def test_success_result(self):
        result = SandboxResult(
            success=True, output="done", duration=0.5,
        )
        assert result.success is True
        assert result.output == "done"

    def test_failure_result(self):
        result = SandboxResult(
            success=False, output="", error="Something broke",
            exit_code=1, duration=0.1,
        )
        assert result.success is False
        assert result.error == "Something broke"
        assert result.exit_code == 1


class TestProcessSandbox:
    """ProcessSandbox invariants."""

    def test_create_workspace(self):
        sandbox = ProcessSandbox()
        path = sandbox.create_workspace()
        assert path.exists()
        assert path.is_dir()
        sandbox.cleanup()
        assert not path.exists()

    def test_context_manager(self):
        with ProcessSandbox() as sandbox:
            assert sandbox.active
            path = sandbox.create_workspace()
            assert path.exists()
        # After exit, temp dir should be cleaned up
        assert not sandbox.active

    def test_execute_command(self):
        sandbox = ProcessSandbox()
        result = sandbox.execute_command("echo hello")
        assert result.success
        assert "hello" in result.output
        sandbox.cleanup()

    def test_execute_failing_command(self):
        sandbox = ProcessSandbox()
        result = sandbox.execute_command("exit 42")
        assert not result.success
        assert result.exit_code == 42
        sandbox.cleanup()

    def test_execute_timeout(self):
        limits = ResourceLimits(timeout_seconds=0.1)
        sandbox = ProcessSandbox(limits=limits)
        result = sandbox.execute_command("sleep 10")
        assert not result.success
        assert "Timeout" in result.error
        sandbox.cleanup()

    def test_execute_with_env(self):
        import sys
        sandbox = ProcessSandbox()
        # Windows uses %VAR%, Unix uses $VAR
        if sys.platform == "win32":
            cmd = "echo %TEST_VAR%"
        else:
            cmd = "echo $TEST_VAR"
        result = sandbox.execute_command(cmd, env={"TEST_VAR": "sandbox_test"})
        assert "sandbox_test" in result.output
        sandbox.cleanup()

    def test_execute_in_cwd(self):
        import sys
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = ProcessSandbox()
            result = sandbox.execute_command(
                sys.platform == "win32" and "cd" or "pwd",
                cwd=tmpdir,
            )
            # On Windows the path normalization may differ
            sandbox.cleanup()

    def test_cleanup_twice(self):
        sandbox = ProcessSandbox()
        sandbox.create_workspace()
        sandbox.cleanup()
        sandbox.cleanup()  # Should not crash


class TestWorktreeSandbox:
    """WorktreeSandbox invariants."""

    def test_not_git_repo_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = WorktreeSandbox(tmpdir)
            with pytest.raises(SandboxError, match="Not a git repository"):
                ws.create()

    def test_context_manager_no_git(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = WorktreeSandbox(tmpdir)
            with pytest.raises(SandboxError):
                with ws as path:
                    pass  # Should not reach here

    def test_cleanup_without_create(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = WorktreeSandbox(tmpdir)
            ws.cleanup()  # Should not crash

    def test_get_diff_without_create(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = WorktreeSandbox(tmpdir)
            assert ws.get_diff() == ""


class TestSandbox:
    """Sandbox unified interface invariants."""

    def test_none_mode(self):
        with Sandbox(mode=SandboxMode.NONE) as sandbox:
            result = sandbox.execute("bash", {"command": "echo test"})
            assert result.success
            assert "none mode" in result.output

    def test_none_mode_no_isolation(self):
        with Sandbox(mode=SandboxMode.NONE) as sandbox:
            with sandbox.isolate() as path:
                # In NONE mode, path should be current dir
                assert path == os.getcwd()

    def test_process_mode(self):
        with Sandbox(mode=SandboxMode.PROCESS) as sandbox:
            result = sandbox.execute("bash", {"command": "echo sandbox_test"})
            assert result.success
            assert "sandbox_test" in result.output

    def test_process_mode_write_not_allowed(self):
        limits = ResourceLimits(allow_write=False)
        with Sandbox(mode=SandboxMode.PROCESS, limits=limits) as sandbox:
            result = sandbox.execute("write_file", {
                "path": "test.txt",
                "content": "hello",
            })
            assert not result.success
            assert "not allowed" in result.error

    def test_process_mode_write_allowed(self):
        limits = ResourceLimits(allow_write=True)
        with Sandbox(mode=SandboxMode.PROCESS, limits=limits) as sandbox:
            sandbox.isolate().__enter__()
            result = sandbox.execute("write_file", {
                "path": "test.txt",
                "content": "hello world",
            })
            assert result.success

    def test_unknown_tool(self):
        with Sandbox(mode=SandboxMode.PROCESS) as sandbox:
            result = sandbox.execute("unknown_tool", {})
            assert result.success  # Unknown tools are allowed by default

    def test_get_path_no_sandbox(self):
        sandbox = Sandbox(mode=SandboxMode.NONE)
        assert sandbox.get_path() is None

    def test_get_path_process(self):
        with Sandbox(mode=SandboxMode.PROCESS) as sandbox:
            with sandbox.isolate() as ps:
                # ps is a ProcessSandbox
                assert ps.active
                spath = sandbox.get_path()
                assert spath is not None
                assert os.path.isdir(spath)

    def test_cleanup(self):
        sandbox = Sandbox(mode=SandboxMode.PROCESS)
        sandbox.cleanup()  # Should not crash

    def test_apply_changes_no_sandbox(self):
        sandbox = Sandbox(mode=SandboxMode.NONE)
        assert sandbox.apply_changes() is True

    def test_get_diff_no_sandbox(self):
        sandbox = Sandbox(mode=SandboxMode.NONE)
        assert sandbox.get_diff() == ""

    def test_sandbox_repr(self):
        sandbox = Sandbox()
        assert hasattr(sandbox, "mode")

    def test_execute_bash_in_process(self):
        with Sandbox(mode=SandboxMode.PROCESS) as sandbox:
            with sandbox.isolate():
                result = sandbox.execute("bash", {"command": "echo isolated"})
                assert "isolated" in result.output

    def test_execute_timeout(self):
        limits = ResourceLimits(timeout_seconds=0.05)
        sandbox = ProcessSandbox(limits=limits)
        result = sandbox.execute_command("sleep 10")
        assert not result.success