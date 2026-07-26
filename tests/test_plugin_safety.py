"""Plugin 生态剩余两支柱: ToolCapabilities 声明强化 + 运行时 JSON Schema 校验.

Corresponds to:
  MASTER.md §12.1 Plugin: 沙箱 (能力声明强制化)
  MASTER.md §12.1 Plugin: schema 强制校验
  src/zall/core/tool.py          — validate_tool_args, assert_capabilities_declared
  src/zall/core/executor.py      — _execute_single 执行前校验
  src/zall/core/plugin_loader.py — _PluginToolWrapper capabilities 默认值

IPR-0: 每个测试含反例.
IPR-3: core/ 只用 stdlib + pydantic (jsonschema 不安装).
"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from zall.core.plugin_loader import _PluginToolWrapper
from zall.core.tool import (
    ToolCapabilities,
    ToolResult,
    ToolScope,
    assert_capabilities_declared,
    get_tool_capabilities,
    validate_tool_args,
)
from zall.core.tool_kind import ToolNamespace


# ──────────────────────────────────────────────────────────────────────────
# validate_tool_args — 单元测试
# ──────────────────────────────────────────────────────────────────────────


class TestValidateToolArgs:
    """validate_tool_args() 轻量 JSON Schema 校验器测试."""

    def test_valid_args_pass(self) -> None:
        """合法 args 返回空列表."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["name"],
        }
        args = {"name": "hello", "count": 42}
        errors = validate_tool_args(schema, args)
        assert errors == []

    def test_missing_required(self) -> None:
        """缺必填字段 -> 非空错误列表."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["name", "path"],
        }
        args = {"name": "hello"}  # missing 'path'
        errors = validate_tool_args(schema, args)
        assert len(errors) == 1
        assert "missing required field: 'path'" in errors[0]

    def test_wrong_type_string(self) -> None:
        """类型不符: string 传了 int -> 错误."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        }
        args = {"name": 42}
        errors = validate_tool_args(schema, args)
        assert len(errors) == 1
        assert "expected type 'string'" in errors[0]

    def test_wrong_type_integer(self) -> None:
        """类型不符: integer 传了 str -> 错误."""
        schema = {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
            },
        }
        args = {"count": "not_a_number"}
        errors = validate_tool_args(schema, args)
        assert len(errors) == 1
        assert "expected type 'integer'" in errors[0]

    def test_wrong_type_boolean(self) -> None:
        """类型不符: boolean 传了 int -> 错误."""
        schema = {
            "type": "object",
            "properties": {
                "flag": {"type": "boolean"},
            },
        }
        args = {"flag": 1}
        errors = validate_tool_args(schema, args)
        assert len(errors) == 1
        assert "expected type 'boolean'" in errors[0]

    def test_enum_violation(self) -> None:
        """值不在 enum -> 错误."""
        schema = {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["read", "write", "append"]},
            },
            "required": ["mode"],
        }
        args = {"mode": "delete"}
        errors = validate_tool_args(schema, args)
        assert len(errors) == 1
        assert "not in enum" in errors[0]

    def test_enum_valid(self) -> None:
        """值在 enum 内 -> 通过."""
        schema = {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["read", "write", "append"]},
            },
            "required": ["mode"],
        }
        args = {"mode": "read"}
        errors = validate_tool_args(schema, args)
        assert errors == []

    def test_empty_schema_allows_anything(self) -> None:
        """空 schema 不报错 (任何 args 都合法)."""
        schema = {}
        args = {"anything": "goes", "numbers": 123}
        errors = validate_tool_args(schema, args)
        assert errors == []

    def test_counterexample_no_crash_on_malformed_schema(self) -> None:
        """Counterexample: schema 本身畸形不崩溃, 返回错误消息."""
        # schema 不是 dict
        errors = validate_tool_args("not_a_dict", {"key": "val"})
        assert isinstance(errors, list)
        assert len(errors) >= 1

        # properties 不是 dict
        errors = validate_tool_args(
            {"type": "object", "properties": "bad"},
            {"key": "val"},
        )
        assert isinstance(errors, list)

        # required 不是 list
        errors = validate_tool_args(
            {"type": "object", "properties": {}, "required": "not_a_list"},
            {"key": "val"},
        )
        assert isinstance(errors, list)

    def test_all_types_pass(self) -> None:
        """所有支持的 type 正确通过."""
        schema = {
            "type": "object",
            "properties": {
                "s": {"type": "string"},
                "n": {"type": "number"},
                "i": {"type": "integer"},
                "b": {"type": "boolean"},
                "o": {"type": "object"},
                "a": {"type": "array"},
                "nl": {"type": "null"},
            },
        }
        args = {
            "s": "text",
            "n": 3.14,
            "i": 42,
            "b": True,
            "o": {"key": "val"},
            "a": [1, 2, 3],
            "nl": None,
        }
        errors = validate_tool_args(schema, args)
        assert errors == []

    def test_unknown_type_is_lenient(self) -> None:
        """未知 type 宽松通过 (不做校验)."""
        schema = {
            "type": "object",
            "properties": {
                "x": {"type": "unknown_custom_type"},
            },
        }
        args = {"x": "anything"}
        errors = validate_tool_args(schema, args)
        assert errors == []

    def test_multiple_errors(self) -> None:
        """多个错误同时返回."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["name", "count"],
        }
        args = {}  # 两个必填都缺
        errors = validate_tool_args(schema, args)
        assert len(errors) == 2


# ──────────────────────────────────────────────────────────────────────────
# assert_capabilities_declared — 单元测试
# ──────────────────────────────────────────────────────────────────────────


class TestAssertCapabilitiesDeclared:
    """assert_capabilities_declared() 测试."""

    def test_builtin_tool_exempted(self) -> None:
        """内置工具 (namespace=ZALL) 豁免 -> 返回 True."""
        tool = MagicMock()
        tool.namespace = ToolNamespace.ZALL
        # 即使没有 capabilities, 内置工具也豁免
        del tool.capabilities
        assert assert_capabilities_declared(tool) is True

    def test_plugin_tool_with_capabilities(self) -> None:
        """插件工具声明了 capabilities -> True."""
        tool = MagicMock()
        tool.namespace = ToolNamespace.PLUGIN
        tool.capabilities = ToolCapabilities(is_read_only=True, tool_scope=ToolScope.Read)
        assert assert_capabilities_declared(tool) is True

    def test_plugin_tool_without_capabilities(self) -> None:
        """Counterexample: 插件工具未声明 capabilities -> False."""
        tool = MagicMock()
        tool.namespace = ToolNamespace.PLUGIN
        # 模拟没有 capabilities 属性
        if hasattr(tool, 'capabilities'):
            del tool.capabilities
        assert assert_capabilities_declared(tool) is False

    def test_plugin_tool_capabilities_is_none(self) -> None:
        """Counterexample: 插件工具 capabilities 为 None -> False."""
        tool = MagicMock()
        tool.namespace = ToolNamespace.PLUGIN
        tool.capabilities = None
        assert assert_capabilities_declared(tool) is False


# ──────────────────────────────────────────────────────────────────────────
# _PluginToolWrapper capabilities 默认值
# ──────────────────────────────────────────────────────────────────────────


class TestPluginToolCapabilitiesDefaults:
    """_PluginToolWrapper capabilities 默认值测试."""

    def test_plugin_tool_defaults_to_write(self) -> None:
        """插件工具未声明 capabilities -> 默认 Write/非只读."""
        inner = MagicMock()
        inner.tool_id = "test_plugin"
        inner.schema = {}
        inner.execute.return_value = ToolResult(success=True, output="ok")
        # 没有 capabilities 属性
        if hasattr(inner, 'capabilities'):
            del inner.capabilities

        wrapper = _PluginToolWrapper(inner)
        caps = get_tool_capabilities(wrapper)
        assert caps.is_read_only is False
        assert caps.tool_scope == ToolScope.Write

    def test_plugin_tool_with_explicit_capabilities(self) -> None:
        """插件工具显式声明了 capabilities -> 保留."""
        inner = MagicMock()
        inner.tool_id = "test_plugin_ro"
        inner.schema = {}
        inner.execute.return_value = ToolResult(success=True, output="ok")
        inner.capabilities = ToolCapabilities(is_read_only=True, tool_scope=ToolScope.Read)

        wrapper = _PluginToolWrapper(inner)
        caps = get_tool_capabilities(wrapper)
        assert caps.is_read_only is True
        assert caps.tool_scope == ToolScope.Read

    def test_builtin_tool_capabilities_preserved(self) -> None:
        """内置工具的 capabilities 不被 _PluginToolWrapper 覆盖."""
        inner = MagicMock()
        inner.tool_id = "builtin_tool"
        inner.schema = {}
        inner.execute.return_value = ToolResult(success=True, output="ok")
        inner.namespace = ToolNamespace.ZALL
        inner.capabilities = ToolCapabilities(is_read_only=True, tool_scope=ToolScope.Read)

        wrapper = _PluginToolWrapper(inner)
        caps = get_tool_capabilities(wrapper)
        assert caps.is_read_only is True
        assert caps.tool_scope == ToolScope.Read


# ──────────────────────────────────────────────────────────────────────────
# get_tool_capabilities — 单元测试
# ──────────────────────────────────────────────────────────────────────────


class TestGetToolCapabilities:
    """get_tool_capabilities() 辅助函数测试."""

    def test_default_when_no_capabilities(self) -> None:
        """没有 capabilities 属性的工具 -> 默认 Write."""
        tool = MagicMock(spec=[])  # 空 spec, 无任何属性
        caps = get_tool_capabilities(tool)
        assert caps.is_read_only is False
        assert caps.tool_scope == ToolScope.Write

    def test_explicit_capabilities_returned(self) -> None:
        """有 capabilities 属性的工具 -> 返回对应的值."""
        tool = MagicMock()
        tool.capabilities = ToolCapabilities(is_read_only=True, tool_scope=ToolScope.Read)
        caps = get_tool_capabilities(tool)
        assert caps.is_read_only is True
        assert caps.tool_scope == ToolScope.Read


# ──────────────────────────────────────────────────────────────────────────
# Executor 集成测试 — 校验失败返回 ToolResult(success=False)
# ──────────────────────────────────────────────────────────────────────────


class TestExecutorSchemaValidation:
    """executor._execute_single 中 schema 校验集成测试."""

    class _FakeValidatableTool:
        """Minimal Tool 实现, 支持 schema 校验测试."""
        __test__ = False

        def __init__(self, schema: dict, tool_id: str = "test_tool") -> None:
            self._schema = schema
            self._tool_id = tool_id
            self.execute_called = False
            self._last_args: dict | None = None

        @property
        def tool_id(self) -> str:
            return self._tool_id

        @property
        def schema(self) -> dict:
            return self._schema

        def execute(self, args: dict) -> ToolResult:
            self.execute_called = True
            self._last_args = args
            return ToolResult(success=True, output="done")

    def _make_mock_loop(self) -> MagicMock:
        """创建一个最小化 mock loop, 支持 executor 调用的所有接口."""
        loop = MagicMock()
        loop._tool_call_count = 0
        loop._tool_usage_counts = {}
        loop._recorder = MagicMock()
        loop._recorder.append = MagicMock()
        loop._emit = MagicMock()
        loop._maybe_checkpoint = MagicMock()
        loop.append_message = MagicMock()
        loop._mark_watermark_dirty = MagicMock()
        loop._ext_registry = None
        return loop

    def test_invalid_args_returns_error_result(self) -> None:
        """executor 校验失败返回 ToolResult(success=False)."""
        from zall.core.executor import ToolExecutor
        from zall.core.gate import GateResult, GateState
        from zall.core.action import Action

        loop = self._make_mock_loop()

        tool = self._FakeValidatableTool(
            schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
            tool_id="test_tool",
        )

        from zall.core.tool import ToolRegistry
        loop._tools = ToolRegistry(tools=(tool,))

        executor = ToolExecutor(loop)

        action = Action(tool_id="test_tool", args={})  # 缺 name
        gate_result = GateResult(
            state=GateState.EXECUTING,
            action_to_execute=action,
            judgement=None,
        )

        executor._execute_single(gate_result, call_id="call_1", step_count=1)

        # 验证: 工具没有被执行 (因为校验失败)
        assert tool.execute_called is False

        # 验证: 结果消息被追加 (校验失败消息)
        assert loop.append_message.called
        call_args = loop.append_message.call_args[0][0]
        assert "SCHEMA VALIDATION FAILED" in call_args.content
        assert "missing required field: 'name'" in call_args.content

        # 验证: recorder 记录了校验失败
        assert loop._recorder.append.called

    def test_valid_args_executes_normally(self) -> None:
        """校验通过 -> 正常执行工具."""
        from zall.core.executor import ToolExecutor
        from zall.core.gate import GateResult, GateState
        from zall.core.action import Action

        loop = self._make_mock_loop()

        tool = self._FakeValidatableTool(
            schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
            tool_id="test_tool",
        )

        from zall.core.tool import ToolRegistry
        loop._tools = ToolRegistry(tools=(tool,))

        executor = ToolExecutor(loop)

        action = Action(tool_id="test_tool", args={"name": "hello"})
        gate_result = GateResult(
            state=GateState.EXECUTING,
            action_to_execute=action,
            judgement=None,
        )

        executor._execute_single(gate_result, call_id="call_1", step_count=1)

        # 验证: 工具被执行了
        assert tool.execute_called is True
        assert tool._last_args == {"name": "hello"}

        # 验证: 结果消息被追加
        assert loop.append_message.called
        call_args = loop.append_message.call_args[0][0]
        assert "done" in call_args.content