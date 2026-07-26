"""Tests for Plugin entry-point discovery mechanism.

Corresponds to:
  MASTER.md §12.1 Plugin 发现机制 (ABSENT -> PARTIAL)
  MASTER.md §8    生态建设
  src/zall/core/plugin_loader.py

IPR-0: invariant tests with counterexamples.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from zall.core.plugin_loader import (
    _PluginToolWrapper,
    discover_tools,
    discover_extensions,
    load_plugin_manifest,
    merge_tools_with_builtins,
)
from zall.core.tool import Tool, ToolResult, get_tool_namespace
from zall.core.tool_kind import ToolNamespace


# ── Fake EntryPoint ──


class _FakeEntryPoint:
    """Minimal mock for importlib.metadata.EntryPoint.

    Provides .name, .value, and .load() interface.
    """

    def __init__(self, name: str, factory: callable,
                 value: str = "module:factory") -> None:
        self.name = name
        self._factory = factory
        self.value = value

    def load(self) -> callable:
        return self._factory


# ── Fake tools ──


class _FakePluginTool:
    """Minimal Tool implementation for plugin testing."""

    def __init__(
        self,
        tool_id: str = "plugin_tool",
        version: str = "1.0.0",
        namespace: ToolNamespace | None = None,
    ) -> None:
        self._id = tool_id
        self._version = version
        self._namespace = namespace

    @property
    def tool_id(self) -> str:
        return self._id

    @property
    def schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def tool_version(self) -> str:
        return self._version

    @property
    def namespace(self) -> ToolNamespace | None:
        return self._namespace

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output="ok")


class _FakeBuiltinTool:
    """Minimal Tool implementation for built-in tool testing."""

    def __init__(self, tool_id: str = "builtin") -> None:
        self._id = tool_id

    @property
    def tool_id(self) -> str:
        return self._id

    @property
    def schema(self) -> dict:
        return {"type": "object", "properties": {}}

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output="builtin")


class _FakeFailingToolFactory:
    """Factory that raises an exception."""

    def __call__(self) -> None:
        raise RuntimeError("factory failed")


class _FakeNonToolFactory:
    """Factory that returns something that is not a Tool."""

    def __call__(self) -> str:
        return "not_a_tool"


# ──────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────


class TestDiscoverTools:
    """discover_tools() invariants."""

    def test_discover_tools_empty_when_no_entry_points(self) -> None:
        """No third-party packages installed -> returns empty list."""
        # Monkeypatch _get_entry_points to return empty list
        with patch("zall.core.plugin_loader._get_entry_points", return_value=[]):
            tools = discover_tools()
        assert tools == []

    def test_discover_tools_loads_fake_entry_point(self) -> None:
        """A valid entry-point factory produces a loaded tool."""
        def factory():
            return _FakePluginTool(tool_id="my_plugin", version="2.0.0")
        fake_eps = [_FakeEntryPoint("my_plugin", factory)]

        with patch("zall.core.plugin_loader._get_entry_points", return_value=fake_eps):
            tools = discover_tools()

        assert len(tools) == 1
        assert tools[0].tool_id == "my_plugin"
        assert tools[0].tool_version == "2.0.0"

    def test_discover_tools_isolates_failures(self) -> None:
        """Counterexample: one failing factory does not prevent others from loading."""
        def good_factory():
            return _FakePluginTool(tool_id="good_tool", version="1.0.0")
        bad_factory = _FakeFailingToolFactory()
        fake_eps = [
            _FakeEntryPoint("good", good_factory),
            _FakeEntryPoint("bad", bad_factory),
            _FakeEntryPoint("also_good", good_factory),
        ]

        with patch("zall.core.plugin_loader._get_entry_points", return_value=fake_eps):
            tools = discover_tools()

        # Both good tools load; the bad one is skipped silently
        assert len(tools) == 2
        assert tools[0].tool_id == "good_tool"
        assert tools[1].tool_id == "good_tool"

    def test_discover_tools_skips_non_tool_return(self) -> None:
        """Counterexample: factory returning non-Tool object is skipped."""
        factory = _FakeNonToolFactory()
        fake_eps = [_FakeEntryPoint("nontool", factory)]

        with patch("zall.core.plugin_loader._get_entry_points", return_value=fake_eps):
            tools = discover_tools()

        assert tools == []


class TestPluginNamespace:
    """Plugin namespace auto-assignment invariants."""

    def test_plugin_namespace_auto_assigned(self) -> None:
        """Plugin tool without namespace gets ToolNamespace.PLUGIN."""
        # _FakePluginTool with namespace=None
        def factory():
            return _FakePluginTool(tool_id="no_ns", namespace=None)
        fake_eps = [_FakeEntryPoint("no_ns", factory)]

        with patch("zall.core.plugin_loader._get_entry_points", return_value=fake_eps):
            tools = discover_tools()

        assert len(tools) == 1
        # _PluginToolWrapper auto-assigns PLUGIN when namespace is absent
        assert get_tool_namespace(tools[0]) == ToolNamespace.PLUGIN

    def test_plugin_namespace_preserved_when_declared(self) -> None:
        """Plugin tool that declares a namespace keeps it."""
        def factory():
            return _FakePluginTool(
                    tool_id="custom_ns", namespace=ToolNamespace.MCP,
                )
        fake_eps = [_FakeEntryPoint("custom_ns", factory)]

        with patch("zall.core.plugin_loader._get_entry_points", return_value=fake_eps):
            tools = discover_tools()

        assert len(tools) == 1
        assert get_tool_namespace(tools[0]) == ToolNamespace.MCP


class TestBuiltinOverride:
    """Builtin tool priority invariants."""

    def test_builtin_overrides_plugin(self) -> None:
        """Counterexample: same tool_id, built-in takes priority over plugin."""
        builtin = _FakeBuiltinTool(tool_id="conflict_tool")
        plugin = _FakePluginTool(tool_id="conflict_tool", version="1.0.0")

        result = merge_tools_with_builtins(
            plugin_tools=[plugin],
            builtin_tools=(builtin,),
        )

        assert len(result) == 1
        assert result[0] is builtin  # built-in wins, plugin discarded

    def test_plugin_added_when_no_conflict(self) -> None:
        """Plugin tool with unique tool_id is added alongside builtins."""
        builtin = _FakeBuiltinTool(tool_id="builtin_1")
        plugin = _FakePluginTool(tool_id="plugin_1", version="1.0.0")

        result = merge_tools_with_builtins(
            plugin_tools=[plugin],
            builtin_tools=(builtin,),
        )

        assert len(result) == 2
        assert result[0].tool_id == "builtin_1"
        assert result[1].tool_id == "plugin_1"


class TestLoadPluginManifest:
    """load_plugin_manifest() invariants."""

    def test_load_plugin_manifest_parses_toml(self) -> None:
        """Parse a valid manifest.toml file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = Path(tmpdir)
            manifest = plugin_dir / "manifest.toml"
            manifest.write_text(
                "[plugin]\n"
                'name = "my-plugin"\n'
                'version = "1.0.0"\n'
                'tools = ["my_tools.py:MyTool"]\n'
            )

            result = load_plugin_manifest(plugin_dir)
            assert result is not None
            assert result["plugin"]["name"] == "my-plugin"
            assert result["plugin"]["version"] == "1.0.0"
            assert result["plugin"]["tools"] == ["my_tools.py:MyTool"]

    def test_load_plugin_manifest_missing_file(self) -> None:
        """Counterexample: missing manifest.toml returns None, not crash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = Path(tmpdir)
            # No manifest.toml file
            result = load_plugin_manifest(plugin_dir)
            assert result is None

    def test_load_plugin_manifest_invalid_toml(self) -> None:
        """Counterexample: malformed TOML returns None, not crash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = Path(tmpdir)
            manifest = plugin_dir / "manifest.toml"
            manifest.write_text("this is not valid toml {{{")

            result = load_plugin_manifest(plugin_dir)
            assert result is None


class TestDiscoverExtensions:
    """discover_extensions() invariants."""

    def test_discover_extensions_empty_when_no_entry_points(self) -> None:
        """No third-party extensions -> returns empty list."""
        with patch("zall.core.plugin_loader._get_entry_points", return_value=[]):
            exts = discover_extensions()
        assert exts == []

    def test_discover_extensions_isolates_failures(self) -> None:
        """Counterexample: one failing extension factory does not block others."""
        class _FakeExt:
            name = "test_ext"
            hooks = {}

        def good_factory():
            return _FakeExt()
        bad_factory = _FakeFailingToolFactory()
        fake_eps = [
            _FakeEntryPoint("good", good_factory),
            _FakeEntryPoint("bad", bad_factory),
        ]

        with patch("zall.core.plugin_loader._get_entry_points", return_value=fake_eps):
            exts = discover_extensions()

        assert len(exts) == 1
        assert exts[0].name == "test_ext"


class TestPluginToolWrapper:
    """_PluginToolWrapper invariants."""

    def test_wrapper_delegates_tool_id_and_execute(self) -> None:
        """Wrapper delegates tool_id, schema, and execute to inner tool."""
        inner = _FakePluginTool(tool_id="wrapped", version="3.0.0")
        wrapper = _PluginToolWrapper(inner)

        assert wrapper.tool_id == "wrapped"
        assert wrapper.schema == {"type": "object", "properties": {}}
        result = wrapper.execute({})
        assert result.success is True
        assert result.output == "ok"

    def test_wrapper_plugin_namespace_when_not_set(self) -> None:
        """Wrapper assigns PLUGIN namespace when inner tool has no namespace."""
        inner = _FakePluginTool(tool_id="no_ns", namespace=None)
        wrapper = _PluginToolWrapper(inner)

        assert wrapper.namespace == ToolNamespace.PLUGIN

    def test_wrapper_preserves_explicit_namespace(self) -> None:
        """Wrapper preserves namespace when inner tool declares one."""
        inner = _FakePluginTool(tool_id="custom", namespace=ToolNamespace.MCP)
        wrapper = _PluginToolWrapper(inner)

        assert wrapper.namespace == ToolNamespace.MCP