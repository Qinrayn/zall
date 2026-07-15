"""Tool + ToolRegistry invariant test (DESIGN.md §4.2 tool层).

IPR-0: each test must contain a counterexample.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zall.core.tool import Tool, ToolRegistry, ToolResult


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


class _FakeTool:
    """Tool stub, for testing Registry."""

    __test__ = False  # pytest 不收

    def __init__(self, tool_id: str = "fake_tool") -> None:
        self._tool_id = tool_id

    @property
    def tool_id(self) -> str:
        return self._tool_id

    @property
    def schema(self) -> dict:
        return {"type": "object", "properties": {}}

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output=f"fake execution of {self._tool_id}")


class _AnotherFakeTool:
    """第二个 Tool stub, 用于测重复 tool_id."""

    __test__ = False

    @property
    def tool_id(self) -> str:
        return "fake_tool"  # 故意与 _FakeTool 相同

    @property
    def schema(self) -> dict:
        return {}

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output="another")


# ──────────────────────────────────────────────────────────────────────────
# ToolResult invariants
# ──────────────────────────────────────────────────────────────────────────


class TestToolResultInvariants:
    """ToolResult invariant."""

    def test_happy_path(self) -> None:
        """Happy path: valid ToolResult constructable."""
        r = ToolResult(success=True, output="done")
        assert r.success is True
        assert r.output == "done"

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后改 success → must raise."""
        r = ToolResult(success=True, output="x")
        with pytest.raises(ValidationError):
            r.success = False  # type: ignore[misc]

    def test_no_tool_history_marker(self) -> None:
        """ToolResult 不携带 tool 历史 (§4.3 核心斩断呼应)."""
        assert ToolResult.__no_tool_history__() is True

    def test_error_field_optional(self) -> None:
        """Happy path: error 可for None (成功时) 或 str (fail时)."""
        ok = ToolResult(success=True, output="done")
        assert ok.error is None

        fail = ToolResult(success=False, output="", error="command not found")
        assert fail.error == "command not found"

    def test_artifacts_dict_known_open(self) -> None:
        """Known OPEN: artifacts dict 可变 (与 Action.args 同型, 不假装)."""
        r = ToolResult(success=True, output="x")
        assert isinstance(r.artifacts, dict)


# ──────────────────────────────────────────────────────────────────────────
# Tool Protocol invariants
# ──────────────────────────────────────────────────────────────────────────


class TestToolProtocolInvariants:
    """Tool Protocol invariant."""

    def test_fake_tool_is_tool(self) -> None:
        """Happy path: _FakeTool 满足 Tool Protocol."""
        assert isinstance(_FakeTool(), Tool)

    def test_bad_object_not_tool(self) -> None:
        """Counterexample: 缺 execute 的对象not Tool."""

        class _Bad:
            @property
            def tool_id(self) -> str:
                return "x"

            @property
            def schema(self) -> dict:
                return {}

        assert not isinstance(_Bad(), Tool)

    def test_tool_id_non_empty(self) -> None:
        """Counterexample: tool_id for空 → 无意义 (与 Action.tool_id 同型约束).

        注: Protocol 不强制non-空 (Protocol 只查属性存在), 但实现应保证.
        本testverify _FakeTool 的 tool_id non-空作for示范.
        """
        tool = _FakeTool("bash")
        assert tool.tool_id != ""


# ──────────────────────────────────────────────────────────────────────────
# ToolRegistry invariants
# ──────────────────────────────────────────────────────────────────────────


class TestToolRegistryInvariants:
    """ToolRegistry invariant."""

    def test_happy_path(self) -> None:
        """Happy path: valid ToolRegistry constructable."""
        reg = ToolRegistry(tools=(_FakeTool("bash"), _FakeTool("read_file")))
        assert len(reg.tools) == 2

    def test_duplicate_tool_id_raises(self) -> None:
        """Counterexample: 两个 tool_id="bash" → must raise (find歧义).

        如果一个实现允许重复, get() returns哪个 Tool 是不确定的 →
        context_judge 判了 SafeLevel 但执行时拿到错误的 Tool.
        """
        with pytest.raises(ValidationError, match="duplicate tool_id"):
            ToolRegistry(tools=(_FakeTool("bash"), _FakeTool("bash")))

    def test_get_existing_tool(self) -> None:
        """Happy path: get 已register的 tool_id → returns Tool."""
        reg = ToolRegistry(tools=(_FakeTool("bash"),))
        tool = reg.get("bash")
        assert tool is not None
        assert tool.tool_id == "bash"

    def test_get_nonexistent_returns_none(self) -> None:
        """Happy path: get 未register的 tool_id → returns None (不 raise)."""
        reg = ToolRegistry(tools=(_FakeTool("bash"),))
        assert reg.get("nonexistent") is None

    def test_has_check(self) -> None:
        """Happy path: has correctlyreturns True/False."""
        reg = ToolRegistry(tools=(_FakeTool("bash"),))
        assert reg.has("bash") is True
        assert reg.has("nonexistent") is False

    def test_tool_ids_property(self) -> None:
        """Happy path: tool_ids returns所有已register id."""
        reg = ToolRegistry(tools=(_FakeTool("bash"), _FakeTool("read_file")))
        assert set(reg.tool_ids) == {"bash", "read_file"}

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后改 tools → must raise."""
        reg = ToolRegistry(tools=(_FakeTool("bash"),))
        with pytest.raises(ValidationError):
            reg.tools = (_FakeTool("read_file"),)  # type: ignore[misc]

    def test_empty_registry_ok(self) -> None:
        """Happy path: 空 Registry constructable (agent 启动时可能无tool)."""
        reg = ToolRegistry()
        assert len(reg.tools) == 0
        assert reg.tool_ids == ()
