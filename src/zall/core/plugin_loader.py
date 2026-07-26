"""zall.core.plugin_loader — Plugin entry-point discovery mechanism.

Corresponds to:
  MASTER.md §8  生态建设 (Plugin ecosystem)
  MASTER.md §12.1 Plugin 发现机制 (ABSENT -> PARTIAL)

Entry-point specification:
  Third-party packages declare entry-points in their pyproject.toml:
    [project.entry-points."zall.tools"]
    my_tool = "my_package.module:factory_function"

    [project.entry-points."zall.extensions"]
    my_ext = "my_package.module:extension_factory"

  Each factory is a callable that returns a Tool instance (or Extension instance).
  Factories receive no arguments; they are expected to be parameterless.

  Local (non-entry-point) plugins can also be loaded from a plugin directory
  containing a manifest.toml file.

IPR constraints:
  IPR-3: stdlib only (importlib.metadata) + zall core. No model SDK.
  IPR-0: Plugin loading failures must not crash the agent loop.
          Each entry-point is loaded in isolation (try/except per factory).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zall._util.logging import get_zall_logger as _get_zall_logger
from zall.core.tool import Tool, ToolResult, get_tool_version
from zall.core.tool_kind import ToolNamespace

_log = _get_zall_logger(__name__)


# ── Compatibility: importlib.metadata entry_points API ──

# Python 3.12+: importlib.metadata.entry_points(group=...) returns a
# list of EntryPoint objects directly.
# Python 3.10/3.11: same API is available.
# Both use the same calling convention.

def _get_entry_points(group: str) -> list[Any]:
    """Get entry points for a given group using importlib.metadata.

    Returns a list of EntryPoint objects, each with .name, .value, .load().
    Returns empty list on any error (e.g. Python version incompatibility).
    """
    try:
        import importlib.metadata as _metadata
        eps = _metadata.entry_points(group=group)
        return list(eps)
    except Exception as exc:
        _log.warning("entry_points(group=%r) failed: %s", group, exc)
        return []


def _load_entry_point(ep: Any) -> Any:
    """Load an entry point's factory and call it.

    Returns the factory's return value, or None on failure.
    """
    try:
        factory = ep.load()
    except Exception as exc:
        _log.warning(
            "plugin entry-point '%s' failed to load: %s", ep.name, exc
        )
        return None

    if not callable(factory):
        _log.warning(
            "plugin entry-point '%s' value is not callable: %r",
            ep.name, factory,
        )
        return None

    try:
        instance = factory()
    except Exception as exc:
        _log.warning(
            "plugin entry-point '%s' factory() raised: %s", ep.name, exc
        )
        return None

    return instance


# ── Plugin Tool Wrapper ──


class _PluginToolWrapper:
    """Wrapper that delegates to a plugin tool but overrides namespace.

    If the wrapped tool does not declare a namespace attribute, it is
    auto-assigned ToolNamespace.PLUGIN. All other attributes are delegated.

    v0.5.1: ToolCapabilities 声明强化 — 插件工具若未声明 capabilities,
    默认 ToolCapabilities(is_read_only=False, tool_scope=ToolScope.Write)
    (最保守). 对应 MASTER.md §12.1 Plugin: 沙箱。

    v0.6.0: 子进程沙箱支持 — 可通过 sandboxed 参数控制是否在隔离子进程
    中执行工具。默认: namespace=PLUGIN 的工具 sandboxed=True (不可信),
    namespace=ZALL 的 False (可信内置)。可通过 manifest 配置覆盖。
    """

    def __init__(
        self,
        tool: Tool,
        sandboxed: bool | None = None,
        entry_point_value: str | None = None,
    ) -> None:
        self._tool = tool
        # None = auto-detect from namespace
        self._sandboxed_override = sandboxed
        # Store entry-point value (e.g., "my_package.module:factory")
        # for subprocess sandbox recreation.
        self._entry_point_value = entry_point_value

    @property
    def sandboxed(self) -> bool:
        """Whether this tool should be executed in a subprocess sandbox.

        Auto-detect: PLUGIN namespace -> sandboxed by default.
        Explicit override via constructor takes precedence.
        """
        if self._sandboxed_override is not None:
            return self._sandboxed_override
        return self.namespace == ToolNamespace.PLUGIN

    @property
    def tool_id(self) -> str:
        return self._tool.tool_id

    @property
    def schema(self) -> dict[str, Any]:
        return self._tool.schema

    def execute(self, args: dict[str, Any]) -> ToolResult:
        """Execute the tool, optionally in a subprocess sandbox.

        If sandboxed=True and entry_point_value is available, the tool is
        executed in an isolated subprocess via SubprocessSandbox.
        Otherwise, delegates directly to the wrapped tool (current behavior).
        """
        if self.sandboxed and self._entry_point_value is not None:
            parts = self._entry_point_value.split(":", 1)
            if len(parts) == 2:
                tool_module, tool_factory = parts
                from zall.core.sandbox import SubprocessSandbox
                sandbox = SubprocessSandbox(timeout=30)
                return sandbox.execute_tool(tool_module, tool_factory, args)
            # Malformed entry point value — fall through to direct execution
        return self._tool.execute(args)

    @property
    def namespace(self) -> ToolNamespace:
        if hasattr(self._tool, "namespace") and self._tool.namespace is not None:
            return self._tool.namespace
        return ToolNamespace.PLUGIN

    @property
    def tool_version(self) -> str:
        return get_tool_version(self._tool)

    @property
    def capabilities(self) -> Any:
        """返回工具的能力声明, 未声明时返回默认 Write/非只读。

        Corresponds to MASTER.md §12.1 Plugin: 沙箱 (能力声明强制化)。
        插件工具必须声明自己是只读还是写, 框架据此决定权限默认值。
        未声明时默认 ToolCapabilities(is_read_only=False, tool_scope=ToolScope.Write)。
        """
        if hasattr(self._tool, 'capabilities') and self._tool.capabilities is not None:
            return self._tool.capabilities
        from zall.core.tool import ToolCapabilities, ToolScope
        return ToolCapabilities(is_read_only=False, tool_scope=ToolScope.Write)

    def __getattr__(self, name: str) -> Any:
        """Delegate any other attribute access (kind, etc.)."""
        return getattr(self._tool, name)


# ── Discovery functions ──


def discover_tools() -> list[Tool]:
    """Scan 'zall.tools' entry-points and load third-party tools.

    Each entry-point's factory() is called and must return a Tool instance.
    If a factory raises, the error is logged and that entry-point is skipped
    (IPR-0: isolation). The plugin tool's namespace is auto-assigned to
    ToolNamespace.PLUGIN if not already declared.

    Returns:
        List of successfully loaded Tool instances (may be empty).
    """
    eps = _get_entry_points("zall.tools")
    tools: list[Tool] = []

    for ep in eps:
        instance = _load_entry_point(ep)
        if instance is None:
            continue

        # Verify the returned instance looks like a Tool
        if not hasattr(instance, "tool_id") or not hasattr(instance, "execute"):
            _log.warning(
                "plugin entry-point '%s' returned object without tool_id/execute: %r",
                ep.name, instance,
            )
            continue

        # Wrap to ensure PLUGIN namespace if not declared
        wrapped = _PluginToolWrapper(instance, entry_point_value=ep.value)

        # v0.5.1: 检查插件工具是否声明了 capabilities
        from zall.core.tool import assert_capabilities_declared
        if not assert_capabilities_declared(instance):
            _log.warning(
                "plugin tool '%s' does not declare capabilities — "
                "defaulting to ToolCapabilities(is_read_only=False, tool_scope=Write). "
                "See MASTER.md §12.1 Plugin: 沙箱",
                wrapped.tool_id,
            )

        tools.append(wrapped)
        _log.info(
            "discovered plugin tool '%s' (v%s) from entry-point '%s'",
            wrapped.tool_id, wrapped.tool_version, ep.name,
        )

    return tools


def discover_extensions() -> list[Any]:
    """Scan 'zall.extensions' entry-points and load extensions.

    Each entry-point's factory() is called and must return an Extension
    (as defined by the Extension protocol in zall.core.extension).
    Failures are isolated per entry-point.

    Returns:
        List of successfully loaded Extension instances (may be empty).
    """
    from zall.core.extension import Extension

    eps = _get_entry_points("zall.extensions")
    extensions: list[Extension] = []

    for ep in eps:
        instance = _load_entry_point(ep)
        if instance is None:
            continue

        # Verify the returned instance looks like an Extension
        if not hasattr(instance, "name") or not hasattr(instance, "hooks"):
            _log.warning(
                "plugin entry-point '%s' returned object without name/hooks: %r",
                ep.name, instance,
            )
            continue

        extensions.append(instance)
        _log.info(
            "discovered plugin extension '%s' from entry-point '%s'",
            instance.name, ep.name,
        )

    return extensions


def load_plugin_manifest(plugin_dir: Path) -> dict | None:
    """Read a plugin manifest.toml from a local plugin directory.

    This is for non-entry-point (local/legacy) plugins that ship a
    manifest.toml file instead of registering via pyproject.toml entry-points.

    Expected manifest format:
        [plugin]
        name = "my-plugin"
        version = "1.0.0"
        tools = ["my_tools.py:MyTool"]   # module:attr 列表

    Args:
        plugin_dir: Path to the plugin directory containing manifest.toml.

    Returns:
        Parsed manifest dict, or None if the file is missing or unparseable.
    """
    manifest_path = plugin_dir / "manifest.toml"
    if not manifest_path.is_file():
        return None

    try:
        import tomli as _tomli
        with open(manifest_path, "rb") as f:
            data = _tomli.load(f)
        return dict(data)
    except ImportError:
        # Python 3.11+ has tomllib in stdlib
        try:
            import tomllib as _tomllib
            with open(manifest_path, "rb") as f:
                data = _tomllib.load(f)
            return dict(data)
        except ImportError:
            _log.warning("no TOML parser available to load %s", manifest_path)
            return None
        except Exception as exc:
            _log.warning("failed to parse manifest %s: %s", manifest_path, exc)
            return None
    except Exception as exc:
        _log.warning("failed to parse manifest %s: %s", manifest_path, exc)
        return None


# ── Merge helpers ──


def merge_tools_with_builtins(
    plugin_tools: list[Tool],
    builtin_tools: tuple[Tool, ...],
) -> tuple[Tool, ...]:
    """Merge plugin tools into builtin tools, with builtin priority.

    If a plugin tool has the same tool_id as a builtin tool, the builtin
    tool wins (plugin tool is discarded). This ensures built-in tools
    cannot be overridden by third-party plugins.

    Args:
        plugin_tools: Tools discovered from entry-points.
        builtin_tools: Built-in tools tuple.

    Returns:
        New tuple with builtin tools first, followed by non-conflicting plugin tools.
    """
    builtin_ids = {t.tool_id for t in builtin_tools}
    filtered_plugins = [t for t in plugin_tools if t.tool_id not in builtin_ids]

    if filtered_plugins:
        _log.info(
            "merged %d plugin tools (%d skipped due to builtin conflict)",
            len(filtered_plugins),
            len(plugin_tools) - len(filtered_plugins),
        )

    return builtin_tools + tuple(filtered_plugins)