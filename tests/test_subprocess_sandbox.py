"""Tests for SubprocessSandbox — plugin tool execution isolation.

Corresponds to:
  MASTER.md §12.1 Plugin: 沙箱 (PARTIAL -> REAL)
  src/zall/core/sandbox.py     — SubprocessSandbox implementation
  src/zall/core/plugin_loader.py — _PluginToolWrapper sandboxed option

IPR-0: invariant tests with counterexamples.
IPR-3: core/ only uses stdlib.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zall.core.plugin_loader import _PluginToolWrapper
from zall.core.sandbox import SubprocessSandbox
from zall.core.tool import ToolResult, ToolCapabilities, ToolScope
from zall.core.tool_kind import ToolNamespace


# ── Helpers ──

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)


def _make_sandbox(timeout: int = 30) -> SubprocessSandbox:
    """Create a SubprocessSandbox with the tests dir pre-configured."""
    return SubprocessSandbox(timeout=timeout)


def _make_sandbox_with_paths(timeout: int = 30) -> SubprocessSandbox:
    """Create a SubprocessSandbox and pass sys_path_entries."""
    sb = SubprocessSandbox(timeout=timeout)
    return sb


# ── SubprocessSandbox tests ──


class TestSubprocessSandbox:
    """SubprocessSandbox execution isolation invariants."""

    def test_sandbox_executes_tool(self) -> None:
        """Sandbox executes a simple fake tool and returns correct result."""
        sandbox = _make_sandbox()
        result = sandbox.execute_tool(
            tool_module="tests._sandbox_test_helpers",
            tool_factory="fake_simple_factory",
            args={"key": "value"},
            sys_path_entries=[_PROJECT_ROOT],
        )
        assert result.success is True
        assert "hello from sandbox" in result.output
        assert result.artifacts.get("called") is True
        assert result.artifacts.get("args") == {"key": "value"}

    def test_sandbox_crash_isolated(self) -> None:
        """Counterexample: tool raises exception, main process is not affected,
        returns error ToolResult."""
        sandbox = _make_sandbox()
        result = sandbox.execute_tool(
            tool_module="tests._sandbox_test_helpers",
            tool_factory="fake_crash_factory",
            args={},
            sys_path_entries=[_PROJECT_ROOT],
        )
        assert result.success is False
        assert "SANDBOX ERROR" in result.output
        assert "RuntimeError" in result.output
        assert "intentional crash" in result.output

    def test_sandbox_timeout(self) -> None:
        """Counterexample: tool infinite loop, timeout returns error ToolResult."""
        sandbox = _make_sandbox(timeout=2)  # Short timeout for test
        result = sandbox.execute_tool(
            tool_module="tests._sandbox_test_helpers",
            tool_factory="fake_infinite_factory",
            args={},
            sys_path_entries=[_PROJECT_ROOT],
        )
        assert result.success is False
        assert "SANDBOX TIMEOUT" in result.output
        assert "2s timeout" in result.output

    def test_sandbox_result_serialization(self) -> None:
        """Complex ToolResult (with artifacts) correctly serialized back."""
        sandbox = _make_sandbox()
        result = sandbox.execute_tool(
            tool_module="tests._sandbox_test_helpers",
            tool_factory="fake_complex_factory",
            args={},
            sys_path_entries=[_PROJECT_ROOT],
        )
        assert result.success is True
        assert result.output == "complex result with artifacts"
        assert result.artifacts["files"] == ["a.txt", "b.txt"]
        assert result.artifacts["count"] == 42
        assert result.artifacts["nested"] == {"key": "value", "items": [1, 2, 3]}

    def test_sandbox_module_not_found(self) -> None:
        """Counterexample: non-existent module returns error, not crash."""
        sandbox = _make_sandbox()
        result = sandbox.execute_tool(
            tool_module="tests.nonexistent_module",
            tool_factory="fake_factory",
            args={},
            sys_path_entries=[_PROJECT_ROOT],
        )
        assert result.success is False
        assert "SANDBOX ERROR" in result.output

    def test_sandbox_empty_stdout(self) -> None:
        """Counterexample: subprocess produces no output -> error ToolResult."""
        sandbox = _make_sandbox()
        # Use a module that exists but doesn't produce output
        result = sandbox.execute_tool(
            tool_module="os",
            tool_factory="getcwd",  # getcwd is a function, not a factory returning a Tool
            args={},
            sys_path_entries=[_PROJECT_ROOT],
        )
        # The factory returns a string, not a Tool, so .execute() will fail
        # But the error is caught by the subprocess runner
        assert result.success is False
        assert "SANDBOX ERROR" in result.output


# ── _PluginToolWrapper sandboxed property tests ──


class TestPluginWrapperSandboxed:
    """_PluginToolWrapper.sandboxed property invariants."""

    def _make_fake_tool(self, namespace: ToolNamespace | None = None) -> MagicMock:
        """Create a minimal mock tool with optional namespace."""
        tool = MagicMock()
        tool.tool_id = "test_tool"
        tool.schema = {"type": "object", "properties": {}}
        tool.execute.return_value = ToolResult(success=True, output="ok")
        if namespace is not None:
            tool.namespace = namespace
        else:
            # Remove namespace to trigger auto-detect
            if hasattr(tool, 'namespace'):
                del tool.namespace
        return tool

    def test_plugin_wrapper_sandboxed_by_default(self) -> None:
        """namespace=PLUGIN -> sandboxed=True by default."""
        tool = self._make_fake_tool(namespace=ToolNamespace.PLUGIN)
        wrapper = _PluginToolWrapper(tool)
        assert wrapper.sandboxed is True

    def test_builtin_tool_not_sandboxed(self) -> None:
        """Counterexample: namespace=ZALL -> sandboxed=False by default."""
        tool = self._make_fake_tool(namespace=ToolNamespace.ZALL)
        wrapper = _PluginToolWrapper(tool)
        assert wrapper.sandboxed is False

    def test_sandboxed_override_false(self) -> None:
        """Explicit sandboxed=False takes precedence over namespace."""
        tool = self._make_fake_tool(namespace=ToolNamespace.PLUGIN)
        wrapper = _PluginToolWrapper(tool, sandboxed=False)
        assert wrapper.sandboxed is False

    def test_sandboxed_override_true(self) -> None:
        """Explicit sandboxed=True takes precedence over namespace."""
        tool = self._make_fake_tool(namespace=ToolNamespace.ZALL)
        wrapper = _PluginToolWrapper(tool, sandboxed=True)
        assert wrapper.sandboxed is True

    def test_default_namespace_plugin_sandboxed(self) -> None:
        """Tool without namespace -> auto PLUGIN -> sandboxed=True."""
        tool = self._make_fake_tool(namespace=None)
        wrapper = _PluginToolWrapper(tool)
        assert wrapper.sandboxed is True

    def test_sandboxed_no_entry_point_falls_back(self) -> None:
        """sandboxed=True but no entry_point_value -> falls back to direct execution."""
        tool = self._make_fake_tool(namespace=ToolNamespace.PLUGIN)
        wrapper = _PluginToolWrapper(tool, sandboxed=True, entry_point_value=None)
        result = wrapper.execute({"test": 1})
        assert result.success is True
        assert result.output == "ok"
        # Verify the wrapped tool's execute was called directly
        tool.execute.assert_called_once_with({"test": 1})

    def test_sandboxed_with_entry_point_value(self) -> None:
        """sandboxed=True with entry_point_value -> uses sandbox."""
        # Create a wrapper that would use sandbox, but with a valid entry point
        tool = self._make_fake_tool(namespace=ToolNamespace.PLUGIN)
        # Patch SubprocessSandbox to avoid actual subprocess execution
        with patch("zall.core.sandbox.SubprocessSandbox") as MockSandbox:
            mock_sandbox_instance = MockSandbox.return_value
            mock_sandbox_instance.execute_tool.return_value = ToolResult(
                success=True, output="sandboxed result"
            )
            wrapper = _PluginToolWrapper(
                tool,
                sandboxed=True,
                entry_point_value="my_module:my_factory",
            )
            result = wrapper.execute({"key": "val"})

            assert result.success is True
            assert result.output == "sandboxed result"
            mock_sandbox_instance.execute_tool.assert_called_once_with(
                "my_module", "my_factory", {"key": "val"}
            )

    def test_mcp_tool_not_sandboxed_by_default(self) -> None:
        """namespace=MCP -> sandboxed=False by default (MCP has its own isolation)."""
        tool = self._make_fake_tool(namespace=ToolNamespace.MCP)
        wrapper = _PluginToolWrapper(tool)
        assert wrapper.sandboxed is False

    def test_codex_tool_not_sandboxed_by_default(self) -> None:
        """namespace=CODEX -> sandboxed=False by default."""
        tool = self._make_fake_tool(namespace=ToolNamespace.CODEX)
        wrapper = _PluginToolWrapper(tool)
        assert wrapper.sandboxed is False


# ── Integration: discover_tools with sandbox ──


class TestDiscoverToolsSandbox:
    """discover_tools() passes entry_point_value to wrapper."""

    def test_discover_tools_passes_entry_point_value(self) -> None:
        """Entry point value is propagated to _PluginToolWrapper."""
        from zall.core.plugin_loader import discover_tools, _get_entry_points

        class _FakeEP:
            """Minimal fake EntryPoint."""
            name = "fake_tool"
            value = "tests._sandbox_test_helpers:fake_simple_factory"
            group = "zall.tools"

            def load(self):
                from tests._sandbox_test_helpers import fake_simple_factory
                return fake_simple_factory

        fake_eps = [_FakeEP()]

        with patch("zall.core.plugin_loader._get_entry_points", return_value=fake_eps):
            tools = discover_tools()

        assert len(tools) == 1
        # The entry_point_value should be stored on the wrapper
        assert tools[0]._entry_point_value == "tests._sandbox_test_helpers:fake_simple_factory"
        # PLUGIN namespace -> sandboxed by default
        assert tools[0].sandboxed is True